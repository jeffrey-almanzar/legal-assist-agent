# CDK app entry point; instantiates the stack.

# app.py
import aws_cdk as cdk

from cdk_stack import LegalAssistanceStack

app = cdk.App()
LegalAssistanceStack(app, 'LegalAssistanceStack')
app.synth()
