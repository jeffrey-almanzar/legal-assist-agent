# Defines the AWS infrastructure using CDK: the app on ECS Fargate behind a load balancer,
# with PostgreSQL + pgvector on RDS. Built as a low-cost proof of concept that `cdk destroy`
# removes completely: no NAT gateways, a single small database with no backups or final
# snapshot, and one-day log retention. Roughly $0.10/hour while deployed.

# cdk_stack.py
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_NAME = 'legal'


class LegalAssistanceStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Public subnets for the load balancer and app; isolated subnets (no internet route)
        # for the database. Skipping NAT gateways saves ~$65/month: the app gets a public IP
        # to reach the Claude API, and only accepts traffic from the load balancer.
        vpc = ec2.Vpc(
            self, 'Vpc',
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name='public', subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name='database', subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
        )

        db = rds.DatabaseInstance(
            self, 'Database',
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            # The password is generated into Secrets Manager; it never appears in the template.
            credentials=rds.Credentials.from_generated_secret('legal'),
            database_name=DB_NAME,
            allocated_storage=20,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            multi_az=False,
            publicly_accessible=False,
            # Proof of concept: no backups, and no final snapshot when the stack is destroyed.
            backup_retention=Duration.days(0),
            delete_automated_backups=True,
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Created with a random placeholder; scripts/deploy.sh stores the real key after the
        # first deploy, so the key never passes through CloudFormation.
        anthropic_key = secretsmanager.Secret(
            self, 'AnthropicApiKey',
            description='Anthropic API key for the legal assistance app',
            removal_policy=RemovalPolicy.DESTROY,
        )

        log_group = logs.LogGroup(
            self, 'AppLogs',
            retention=logs.RetentionDays.ONE_DAY,
            removal_policy=RemovalPolicy.DESTROY,
        )

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, 'App',
            cluster=ecs.Cluster(self, 'Cluster', vpc=vpc),
            cpu=1024,
            memory_limit_mib=2048,
            desired_count=1,
            # Graviton (ARM) is cheaper, and Apple Silicon Macs build ARM images natively.
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            task_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
            public_load_balancer=True,
            # Startup applies the schema and loads the knowledge base before serving.
            health_check_grace_period=Duration.minutes(3),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_asset(str(REPO_ROOT), platform=ecr_assets.Platform.LINUX_ARM64),
                container_port=8000,
                # With a single task, running the idempotent schema and seed steps at startup
                # is simplest. With several tasks, run them as a one-off task instead.
                command=['sh', '-c', 'python -m scripts.init_db && python -m scripts.seed '
                         '&& exec uvicorn app.main:app --host 0.0.0.0 --port 8000'],
                environment={
                    'DB_HOST': db.db_instance_endpoint_address,
                    'DB_PORT': db.db_instance_endpoint_port,
                    'DB_NAME': DB_NAME,
                    # Rate limits should apply to visitors, not the load balancer's address.
                    'TRUST_PROXY_HEADERS': '1',
                },
                secrets={
                    'DB_USER': ecs.Secret.from_secrets_manager(db.secret, 'username'),
                    'DB_PASSWORD': ecs.Secret.from_secrets_manager(db.secret, 'password'),
                    'ANTHROPIC_API_KEY': ecs.Secret.from_secrets_manager(anthropic_key),
                },
                log_driver=ecs.LogDrivers.aws_logs(stream_prefix='app', log_group=log_group),
            ),
        )
        service.target_group.configure_health_check(path='/health', healthy_http_codes='200')
        db.connections.allow_default_port_from(service.service, 'App to PostgreSQL')

        CfnOutput(self, 'AppUrl', value=f'http://{service.load_balancer.load_balancer_dns_name}')
        CfnOutput(self, 'AnthropicSecretArn', value=anthropic_key.secret_arn)
        CfnOutput(self, 'ClusterName', value=service.cluster.cluster_name)
        CfnOutput(self, 'ServiceName', value=service.service.service_name)
