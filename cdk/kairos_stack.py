"""Kairos CDK Stack - Lambda Functions, SNS, and Function URLs."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_sns as sns,
    aws_ssm as ssm,
)
from constructs import Construct

# SSM Parameter names (stored as SecureString)
SSM_BLAND_API_KEY = "/kairos/bland-api-key"
SSM_ANTHROPIC_API_KEY = "/kairos/anthropic-api-key"
SSM_MY_PHONE = "/kairos/my-phone-number"


class KairosStack(Stack):
    """Main infrastructure stack for Kairos Slice 1."""

    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # === SSM Parameter References (for phone number only - not a secret) ===
        my_phone = ssm.StringParameter.value_for_string_parameter(
            self, SSM_MY_PHONE
        )

        # === SNS Topic for SMS ===
        sms_topic = sns.Topic(
            self,
            "KairosSMSTopic",
            display_name="Kairos Debrief Summaries",
        )

        # SMS Subscription
        sns.Subscription(
            self,
            "SMSSubscription",
            topic=sms_topic,
            protocol=sns.SubscriptionProtocol.SMS,
            endpoint=my_phone,
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
                "SNS_TOPIC_ARN": sms_topic.topic_arn,
                "POWERTOOLS_SERVICE_NAME": "kairos-webhook",
            },
            **common_lambda_props,
        )
        sms_topic.grant_publish(webhook_fn)

        # Grant direct SMS publish permission (phone numbers aren't ARNs, so resource is *)
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                resources=["*"],  # Required for direct SMS to phone numbers
            )
        )

        # Grant SSM read access for Anthropic API key
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{SSM_ANTHROPIC_API_KEY}"
                ],
            )
        )

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

        trigger_url = trigger_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
            ),
        )

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

