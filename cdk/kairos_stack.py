"""Kairos CDK Stack - Lambda Functions, SES Email, DynamoDB, and Function URLs."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_scheduler as scheduler,
    aws_sns as sns,
    aws_ssm as ssm,
)
from constructs import Construct

# SSM Parameter names (stored as SecureString)
SSM_BLAND_API_KEY = "/kairos/bland-api-key"
SSM_BLAND_WEBHOOK_SECRET = "/kairos/bland-webhook-secret"
SSM_ANTHROPIC_API_KEY = "/kairos/anthropic-api-key"
SSM_MY_EMAIL = "/kairos/my-email"

# Slice 2: Google Calendar
SSM_GOOGLE_CLIENT_ID = "/kairos/google-client-id"
SSM_GOOGLE_CLIENT_SECRET = "/kairos/google-client-secret"
SSM_GOOGLE_REFRESH_TOKEN = "/kairos/google-refresh-token"

# Slice 2E: Twilio SMS
SSM_TWILIO_ACCOUNT_SID = "/kairos/twilio-account-sid"
SSM_TWILIO_AUTH_TOKEN = "/kairos/twilio-auth-token"
SSM_TWILIO_FROM_NUMBER = "/kairos/twilio-from-number"


class KairosStack(Stack):
    """Main infrastructure stack for Kairos."""

    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # === SSM Parameter References ===
        my_email = ssm.StringParameter.value_for_string_parameter(
            self, SSM_MY_EMAIL
        )

        # === DynamoDB Table for Call Deduplication ===
        dedup_table = dynamodb.Table(
            self,
            "CallDeduplicationTable",
            table_name="kairos-call-dedup",
            partition_key=dynamodb.Attribute(
                name="call_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
            time_to_live_attribute="ttl",  # Auto-cleanup old entries
        )

        # === SNS Topic for Alarms ===
        alarm_topic = sns.Topic(
            self,
            "KairosAlarmTopic",
            display_name="Kairos Error Alerts",
        )
        # Subscribe your email to alarm notifications
        sns.Subscription(
            self,
            "AlarmEmailSubscription",
            topic=alarm_topic,
            protocol=sns.SubscriptionProtocol.EMAIL,
            endpoint=my_email,
        )

        # === Lambda Layer for Dependencies ===
        deps_layer = lambda_.LayerVersion(
            self,
            "KairosDepsLayer",
            code=lambda_.Code.from_asset(str(Path(__file__).parent.parent / "layer")),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Kairos dependencies: pydantic, httpx, anthropic, powertools",
        )

        # === Common Lambda Config ===
        common_lambda_props = {
            "runtime": lambda_.Runtime.PYTHON_3_12,
            "architecture": lambda_.Architecture.ARM_64,
            "memory_size": 256,
            "timeout": Duration.seconds(30),
            "layers": [deps_layer],
            "log_retention": logs.RetentionDays.ONE_WEEK,
        }

        src_path = str(Path(__file__).parent.parent / "src")

        # === Webhook Lambda (deploy first to get URL) ===
        webhook_fn = lambda_.Function(
            self,
            "WebhookFunction",
            function_name="kairos-webhook",
            code=lambda_.Code.from_asset(src_path),
            handler="handlers.webhook.handler",
            environment={
                "SSM_ANTHROPIC_API_KEY": SSM_ANTHROPIC_API_KEY,
                "SSM_BLAND_WEBHOOK_SECRET": SSM_BLAND_WEBHOOK_SECRET,
                "SENDER_EMAIL": my_email,  # Must be verified in SES
                "RECIPIENT_EMAIL": my_email,  # Send to self for MVP
                "DEDUP_TABLE_NAME": dedup_table.table_name,
                "POWERTOOLS_SERVICE_NAME": "kairos-webhook",
            },
            **common_lambda_props,
        )

        # Grant DynamoDB access for deduplication
        dedup_table.grant_read_write_data(webhook_fn)

        # Grant SES send email permission
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=["*"],  # SES doesn't support resource-level permissions well
            )
        )

        # Grant SSM read access for Anthropic API key, webhook secret, and Twilio credentials
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_ANTHROPIC_API_KEY}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_BLAND_WEBHOOK_SECRET}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_TWILIO_ACCOUNT_SID}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_TWILIO_AUTH_TOKEN}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_TWILIO_FROM_NUMBER}",
                ],
            )
        )

        # === CloudWatch Alarm for Webhook Errors ===
        webhook_errors = webhook_fn.metric_errors(period=Duration.minutes(5))
        webhook_alarm = cloudwatch.Alarm(
            self,
            "WebhookErrorAlarm",
            metric=webhook_errors,
            threshold=1,
            evaluation_periods=1,
            alarm_description="Kairos webhook Lambda errors",
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        webhook_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        webhook_url = webhook_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
            ),
        )

        # === Trigger Lambda ===
        trigger_fn = lambda_.Function(
            self,
            "TriggerFunction",
            function_name="kairos-trigger",
            code=lambda_.Code.from_asset(src_path),
            handler="handlers.trigger.handler",
            environment={
                "SSM_BLAND_API_KEY": SSM_BLAND_API_KEY,
                "WEBHOOK_URL": webhook_url.url,
                "POWERTOOLS_SERVICE_NAME": "kairos-trigger",
            },
            **common_lambda_props,
        )

        # Grant SSM read access for Bland API key
        trigger_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_BLAND_API_KEY}"
                ],
            )
        )

        # === CloudWatch Alarm for Trigger Errors ===
        trigger_errors = trigger_fn.metric_errors(period=Duration.minutes(5))
        trigger_alarm = cloudwatch.Alarm(
            self,
            "TriggerErrorAlarm",
            metric=trigger_errors,
            threshold=1,
            evaluation_periods=1,
            alarm_description="Kairos trigger Lambda errors",
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        trigger_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        trigger_url = trigger_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
            ),
        )

        # ========================================
        # SLICE 2: Google Calendar Integration
        # ========================================

        # === DynamoDB Table for Meetings ===
        meetings_table = dynamodb.Table(
            self,
            "MeetingsTable",
            table_name="kairos-meetings",
            partition_key=dynamodb.Attribute(
                name="user_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="meeting_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
            time_to_live_attribute="ttl",
        )

        # === DynamoDB Table for Transcripts (Slice 3) ===
        transcripts_table = dynamodb.Table(
            self,
            "TranscriptsTable",
            table_name="kairos-transcripts",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
            time_to_live_attribute="ttl",
        )

        # === DynamoDB Table for Entities (Slice 3) ===
        entities_table = dynamodb.Table(
            self,
            "EntitiesTable",
            table_name="kairos-entities",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
        )

        # GSI1: Query entities by type (e.g., all Person entities for a user)
        entities_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="gsi1pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gsi1sk", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI2: Query entities by email (deterministic lookup for Person entities)
        entities_table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(
                name="gsi2pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gsi2sk", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # === DynamoDB Table for Mentions (Slice 3) ===
        mentions_table = dynamodb.Table(
            self,
            "MentionsTable",
            table_name="kairos-mentions",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
        )

        # GSI1: Query mentions by linked entity
        mentions_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="gsi1pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gsi1sk", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI2: Query mentions by resolution state (e.g., all ambiguous mentions)
        mentions_table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(
                name="gsi2pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gsi2sk", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # === DynamoDB Table for Edges (Slice 3) ===
        # Dual-write pattern: both EDGEOUT and EDGEIN items stored in same table
        edges_table = dynamodb.Table(
            self,
            "EdgesTable",
            table_name="kairos-edges",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
        )

        # === DynamoDB Table for Entity Aliases (Slice 3) ===
        # Inverted index for fast alias → entity lookups
        entity_aliases_table = dynamodb.Table(
            self,
            "EntityAliasesTable",
            table_name="kairos-entity-aliases",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
        )

        # GSI1: Query aliases by entity (for merge operations)
        entity_aliases_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="gsi1pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gsi1sk", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # === Calendar Webhook Lambda ===
        calendar_webhook_fn = lambda_.Function(
            self,
            "CalendarWebhookFunction",
            function_name="kairos-calendar-webhook",
            code=lambda_.Code.from_asset(src_path),
            handler="handlers.calendar_webhook.handler",
            environment={
                "MEETINGS_TABLE_NAME": meetings_table.table_name,
                "USER_ID": "default",  # MVP: single user
                "POWERTOOLS_SERVICE_NAME": "kairos-calendar-webhook",
            },
            **common_lambda_props,
        )

        # Grant DynamoDB access
        meetings_table.grant_read_write_data(calendar_webhook_fn)

        # Grant SSM read access for Google OAuth credentials
        calendar_webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_CLIENT_ID}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_CLIENT_SECRET}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_REFRESH_TOKEN}",
                ],
            )
        )

        # Function URL for Google Calendar push notifications
        calendar_webhook_url = calendar_webhook_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
            ),
        )

        # === CloudWatch Alarm for Calendar Webhook Errors ===
        calendar_webhook_errors = calendar_webhook_fn.metric_errors(period=Duration.minutes(5))
        calendar_webhook_alarm = cloudwatch.Alarm(
            self,
            "CalendarWebhookErrorAlarm",
            metric=calendar_webhook_errors,
            threshold=1,
            evaluation_periods=1,
            alarm_description="Kairos calendar webhook Lambda errors",
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        calendar_webhook_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # ========================================
        # SLICE 2 MVP: User State & Idempotency
        # ========================================

        # === DynamoDB Table for User State ===
        user_state_table = dynamodb.Table(
            self,
            "UserStateTable",
            table_name="kairos-user-state",
            partition_key=dynamodb.Attribute(
                name="user_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
        )

        # === DynamoDB Table for Idempotency ===
        # Used for SMS dedup, call batch dedup, and daily leases
        idempotency_table = dynamodb.Table(
            self,
            "IdempotencyTable",
            table_name="kairos-idempotency",
            partition_key=dynamodb.Attribute(
                name="idempotency_key", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For dev - change for prod
            time_to_live_attribute="ttl",  # Auto-cleanup old entries
        )

        # ========================================
        # SLICE 2 MVP: Daily Planning & Scheduling
        # ========================================

        # === Prompt Sender Lambda (initiates daily debrief calls) ===
        # We need to create this first to get its ARN for the scheduler
        prompt_sender_fn = lambda_.Function(
            self,
            "PromptSenderFunction",
            function_name="kairos-prompt-sender",
            code=lambda_.Code.from_asset(src_path),
            handler="handlers.prompt_sender.handler",
            environment={
                "USER_STATE_TABLE": user_state_table.table_name,
                "IDEMPOTENCY_TABLE": idempotency_table.table_name,
                "MEETINGS_TABLE": meetings_table.table_name,
                "SSM_BLAND_API_KEY": SSM_BLAND_API_KEY,
                "SSM_TWILIO_ACCOUNT_SID": SSM_TWILIO_ACCOUNT_SID,
                "SSM_TWILIO_AUTH_TOKEN": SSM_TWILIO_AUTH_TOKEN,
                "SSM_TWILIO_FROM_NUMBER": SSM_TWILIO_FROM_NUMBER,
                "WEBHOOK_URL": webhook_url.url,
                "POWERTOOLS_SERVICE_NAME": "kairos-prompt-sender",
            },
            timeout=Duration.seconds(60),  # Longer timeout for API calls
            **{k: v for k, v in common_lambda_props.items() if k != "timeout"},
        )

        # Grant DynamoDB access
        user_state_table.grant_read_write_data(prompt_sender_fn)
        idempotency_table.grant_read_write_data(prompt_sender_fn)
        meetings_table.grant_read_data(prompt_sender_fn)

        # Grant SSM read access for Bland API key and user phone
        prompt_sender_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/kairos/*",
                ],
            )
        )

        # === CloudWatch Alarm for Prompt Sender Errors ===
        prompt_sender_errors = prompt_sender_fn.metric_errors(period=Duration.minutes(5))
        prompt_sender_alarm = cloudwatch.Alarm(
            self,
            "PromptSenderErrorAlarm",
            metric=prompt_sender_errors,
            threshold=1,
            evaluation_periods=1,
            alarm_description="Kairos prompt sender Lambda errors",
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        prompt_sender_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # ========================================
        # SLICE 2E: SMS Webhook (Twilio Inbound)
        # ========================================

        # === SMS Webhook Lambda ===
        sms_webhook_fn = lambda_.Function(
            self,
            "SmsWebhookFunction",
            function_name="kairos-sms-webhook",
            code=lambda_.Code.from_asset(src_path),
            handler="handlers.sms_webhook.handler",
            environment={
                "USER_STATE_TABLE": user_state_table.table_name,
                "IDEMPOTENCY_TABLE": idempotency_table.table_name,
                "MEETINGS_TABLE": meetings_table.table_name,
                "SSM_TWILIO_AUTH_TOKEN": SSM_TWILIO_AUTH_TOKEN,
                "SSM_ANTHROPIC_API_KEY": SSM_ANTHROPIC_API_KEY,
                "SSM_BLAND_API_KEY": SSM_BLAND_API_KEY,
                "WEBHOOK_URL": webhook_url.url,
                "POWERTOOLS_SERVICE_NAME": "kairos-sms-webhook",
            },
            timeout=Duration.seconds(60),  # Longer timeout for LLM + Bland calls
            **{k: v for k, v in common_lambda_props.items() if k != "timeout"},
        )

        # Grant DynamoDB access
        user_state_table.grant_read_write_data(sms_webhook_fn)
        idempotency_table.grant_read_write_data(sms_webhook_fn)
        meetings_table.grant_read_data(sms_webhook_fn)

        # Grant SSM read access for Twilio, Anthropic, and Bland API keys
        sms_webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_TWILIO_AUTH_TOKEN}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_ANTHROPIC_API_KEY}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_BLAND_API_KEY}",
                ],
            )
        )

        # Function URL for Twilio webhook callbacks
        sms_webhook_url = sms_webhook_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
            ),
        )

        # === CloudWatch Alarm for SMS Webhook Errors ===
        sms_webhook_errors = sms_webhook_fn.metric_errors(period=Duration.minutes(5))
        sms_webhook_alarm = cloudwatch.Alarm(
            self,
            "SmsWebhookErrorAlarm",
            metric=sms_webhook_errors,
            threshold=1,
            evaluation_periods=1,
            alarm_description="Kairos SMS webhook Lambda errors",
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        sms_webhook_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # === IAM Role for EventBridge Scheduler ===
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            role_name="kairos-scheduler-role",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            description="Role for EventBridge Scheduler to invoke Kairos Lambdas",
        )

        # Grant permission to invoke prompt sender Lambda
        prompt_sender_fn.grant_invoke(scheduler_role)

        # === Daily Planning Lambda ===
        daily_plan_fn = lambda_.Function(
            self,
            "DailyPlanFunction",
            function_name="kairos-daily-plan",
            code=lambda_.Code.from_asset(src_path),
            handler="handlers.daily_plan_prompt.handler",
            environment={
                "USER_STATE_TABLE": user_state_table.table_name,
                "IDEMPOTENCY_TABLE": idempotency_table.table_name,
                "PROMPT_SENDER_ARN": prompt_sender_fn.function_arn,
                "SCHEDULER_ROLE_ARN": scheduler_role.role_arn,
                "MVP_USER_ID": "user-001",  # MVP: single user
                "POWERTOOLS_SERVICE_NAME": "kairos-daily-plan",
            },
            timeout=Duration.seconds(60),  # Longer timeout for calendar API calls
            **{k: v for k, v in common_lambda_props.items() if k != "timeout"},
        )

        # Add calendar webhook URL for push notification setup
        daily_plan_fn.add_environment("CALENDAR_WEBHOOK_URL", calendar_webhook_url.url)

        # Grant DynamoDB access
        user_state_table.grant_read_write_data(daily_plan_fn)
        idempotency_table.grant_read_write_data(daily_plan_fn)

        # Grant SSM read access for Google OAuth credentials
        daily_plan_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_CLIENT_ID}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_CLIENT_SECRET}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_REFRESH_TOKEN}",
                ],
            )
        )

        # Grant EventBridge Scheduler permissions
        daily_plan_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                ],
                resources=[
                    f"arn:aws:scheduler:{self.region}:{self.account}:schedule/default/kairos-*",
                ],
            )
        )

        # Grant permission to pass the scheduler role
        daily_plan_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_role.role_arn],
            )
        )

        # Also grant scheduler permissions to calendar webhook (for reconciliation)
        calendar_webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                ],
                resources=[
                    f"arn:aws:scheduler:{self.region}:{self.account}:schedule/default/kairos-*",
                ],
            )
        )
        calendar_webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_role.role_arn],
            )
        )

        # Add environment variables for debrief event change detection
        calendar_webhook_fn.add_environment("USER_STATE_TABLE", user_state_table.table_name)
        calendar_webhook_fn.add_environment("SCHEDULER_ROLE_ARN", scheduler_role.role_arn)
        calendar_webhook_fn.add_environment("PROMPT_SENDER_FUNCTION_NAME", "kairos-prompt-sender")
        calendar_webhook_fn.add_environment("USER_ID", "user-001")  # MVP: single user

        # Grant calendar webhook access to user state table
        user_state_table.grant_read_write_data(calendar_webhook_fn)

        # Slice 3: Grant calendar webhook access to knowledge graph tables
        calendar_webhook_fn.add_environment("ENTITIES_TABLE", entities_table.table_name)
        calendar_webhook_fn.add_environment("ALIASES_TABLE", entity_aliases_table.table_name)
        entities_table.grant_read_write_data(calendar_webhook_fn)
        entity_aliases_table.grant_read_write_data(calendar_webhook_fn)

        # Grant STS access for getting account ID
        calendar_webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
            )
        )

        # ========================================
        # SLICE 2 MVP: Webhook Retry Support
        # ========================================
        # Add retry-related environment variables to webhook Lambda
        # Note: We use function name instead of ARN to avoid circular dependency
        # (prompt_sender references webhook_url, webhook needs prompt_sender ARN)
        webhook_fn.add_environment("USER_STATE_TABLE", user_state_table.table_name)
        webhook_fn.add_environment("IDEMPOTENCY_TABLE", idempotency_table.table_name)
        webhook_fn.add_environment("PROMPT_SENDER_FUNCTION_NAME", "kairos-prompt-sender")
        webhook_fn.add_environment("SCHEDULER_ROLE_ARN", scheduler_role.role_arn)

        # Grant webhook Lambda access to user state and idempotency tables
        user_state_table.grant_read_write_data(webhook_fn)
        idempotency_table.grant_read_write_data(webhook_fn)

        # Grant webhook Lambda permission to create/update schedules (for retries)
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                ],
                resources=[
                    f"arn:aws:scheduler:{self.region}:{self.account}:schedule/default/kairos-*",
                ],
            )
        )

        # Grant webhook Lambda permission to pass the scheduler role
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_role.role_arn],
            )
        )

        # Grant webhook Lambda permission to get caller identity (for account ID)
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
            )
        )

        # Add meetings table access for marking meetings debriefed
        webhook_fn.add_environment("MEETINGS_TABLE", meetings_table.table_name)
        meetings_table.grant_read_write_data(webhook_fn)

        webhook_fn.add_environment("TRANSCRIPTS_TABLE", transcripts_table.table_name)
        transcripts_table.grant_read_write_data(webhook_fn)

        # Add Knowledge Graph tables access (Slice 3)
        webhook_fn.add_environment("ENTITIES_TABLE", entities_table.table_name)
        webhook_fn.add_environment("MENTIONS_TABLE", mentions_table.table_name)
        webhook_fn.add_environment("EDGES_TABLE", edges_table.table_name)
        webhook_fn.add_environment("ENTITY_ALIASES_TABLE", entity_aliases_table.table_name)

        entities_table.grant_read_write_data(webhook_fn)
        mentions_table.grant_read_write_data(webhook_fn)
        edges_table.grant_read_write_data(webhook_fn)
        entity_aliases_table.grant_read_write_data(webhook_fn)


        # Add Google Calendar SSM access for deleting debrief event
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_CLIENT_ID}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_CLIENT_SECRET}",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_GOOGLE_REFRESH_TOKEN}",
                ],
            )
        )

        # === EventBridge Scheduler: Daily 08:00 Europe/London ===
        # Using L1 construct since L2 for Scheduler is not yet available
        daily_schedule = scheduler.CfnSchedule(
            self,
            "DailyPlanSchedule",
            name="kairos-daily-plan",
            schedule_expression="cron(0 8 * * ? *)",  # 08:00 every day
            schedule_expression_timezone="Europe/London",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF",
            ),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=daily_plan_fn.function_arn,
                role_arn=scheduler_role.role_arn,
                input='{"source": "scheduled"}',
            ),
            state="ENABLED",
            description="Daily planning at 08:00 Europe/London",
        )

        # Grant scheduler permission to invoke daily plan Lambda
        daily_plan_fn.grant_invoke(scheduler_role)

        # === CloudWatch Alarm for Daily Plan Errors ===
        daily_plan_errors = daily_plan_fn.metric_errors(period=Duration.minutes(5))
        daily_plan_alarm = cloudwatch.Alarm(
            self,
            "DailyPlanErrorAlarm",
            metric=daily_plan_errors,
            threshold=1,
            evaluation_periods=1,
            alarm_description="Kairos daily plan Lambda errors",
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        daily_plan_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # === Outputs ===
        cdk.CfnOutput(
            self,
            "TriggerUrl",
            value=trigger_url.url,
            description="URL to trigger a debrief call",
        )
        cdk.CfnOutput(
            self,
            "WebhookUrl",
            value=webhook_url.url,
            description="Webhook URL for Bland AI callbacks",
        )
        cdk.CfnOutput(
            self,
            "CalendarWebhookUrl",
            value=calendar_webhook_url.url,
            description="Webhook URL for Google Calendar push notifications",
        )
        cdk.CfnOutput(
            self,
            "SmsWebhookUrl",
            value=sms_webhook_url.url,
            description="Webhook URL for Twilio SMS callbacks",
        )
