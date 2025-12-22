#!/usr/bin/env python3
"""CDK application entry point."""

import aws_cdk as cdk

from kairos_stack import KairosStack

app = cdk.App()

KairosStack(
    app,
    "KairosStack",
    env=cdk.Environment(
        account=cdk.Aws.ACCOUNT_ID,
        region="eu-west-1",
    ),
    description="Kairos Chief of Staff - Slice 1: Mock Event Debrief",
)

app.synth()

