# Defines the AWS infrastructure using CDK.

# cdk_stack.py
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from constructs import Construct

class LegalAssistanceStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        vpc = ec2.Vpc(self, 'VPC')
        db = rds.DatabaseInstance(
            self, 'PostgreSQL',
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            vpc=vpc,
            storage_encrypted=True
        )
