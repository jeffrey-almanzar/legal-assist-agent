# CDK app entry point; instantiates the stack in the account and region of your AWS credentials.

# app.py
import os

import aws_cdk as cdk

from cdk_stack import LegalAssistanceStack

app = cdk.App()
LegalAssistanceStack(
    app, 'LegalAssistanceStack',
    env=cdk.Environment(account=os.environ.get('CDK_DEFAULT_ACCOUNT'), region=os.environ.get('CDK_DEFAULT_REGION')),
)
app.synth()
