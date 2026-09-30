"""Prepare KMS, disabled email/monitor worker, authenticated SES feedback and alerts.

Requires private network preparation and a Linux Python 3.13 worker ZIP argument.
Preserves existing application secrets. No customer mail or App Runner change here.
"""
import json
import pathlib
import secrets
import sys

import boto3
from botocore.exceptions import ClientError

REGION='ap-south-1'
ACCOUNT='085193942944'
NAME='vox-photoshoot-notifications'


def main(zip_path):
    network=json.loads(pathlib.Path('/private/tmp/vox-admin-network.json').read_text())
    iam=boto3.client('iam');sm=boto3.client('secretsmanager',region_name=REGION)
    kms=boto3.client('kms',region_name=REGION);sns=boto3.client('sns',region_name=REGION)
    ses=boto3.client('sesv2',region_name=REGION);lam=boto3.client('lambda',region_name=REGION)
    logs=boto3.client('logs',region_name=REGION);events=boto3.client('events',region_name=REGION)
    alias='alias/vox-photoshoot-admin'
    key=next((a.get('TargetKeyId') for a in kms.list_aliases()['Aliases'] if a['AliasName']==alias),None)
    if not key:
        key=kms.create_key(Description='Donna Photoshoot administrator authenticator secrets',
                           Tags=[{'TagKey':'Application','TagValue':'vox-photoshoot'}])['KeyMetadata']['KeyId']
        kms.create_alias(AliasName=alias,TargetKeyId=key)
        kms.enable_key_rotation(KeyId=key)
    key_arn=kms.describe_key(KeyId=key)['KeyMetadata']['Arn']
    app=sm.get_secret_value(SecretId='vox-photoshoot/env')
    values=json.loads(app['SecretString'])
    values.setdefault('ACCOUNT_LINK_KEY',secrets.token_hex(32))
    values['ADMIN_KMS_KEY']=key_arn
    assert sm.get_secret_value(SecretId=app['ARN'])['VersionId']==app['VersionId'], 'app secrets changed concurrently'
    sm.put_secret_value(SecretId=app['ARN'],SecretString=json.dumps(values))
    worker_values={'DATABASE_URL':values['DATABASE_URL'],'ACCOUNT_LINK_KEY':values['ACCOUNT_LINK_KEY'],
                   'NOTIFICATIONS_ENABLED':'0','NOTIFICATIONS_SINCE':'2099-01-01T00:00:00Z'}
    try:
        existing=sm.get_secret_value(SecretId='vox-photoshoot/notifications')
        worker_values=json.loads(existing['SecretString'])
        worker_arn=existing['ARN']
    except sm.exceptions.ResourceNotFoundException:
        worker_arn=sm.create_secret(Name='vox-photoshoot/notifications',SecretString=json.dumps(worker_values))['ARN']
    feedback=sns.create_topic(Name='vox-photoshoot-email-feedback')['TopicArn']
    alerts=sns.create_topic(Name='vox-photoshoot-alerts')['TopicArn']
    feedback_policy={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'ses.amazonaws.com'},
        'Action':'SNS:Publish','Resource':feedback,'Condition':{'StringEquals':{'AWS:SourceAccount':ACCOUNT},
        'ArnLike':{'AWS:SourceArn':f'arn:aws:ses:{REGION}:{ACCOUNT}:configuration-set/vox-photoshoot'}}}]}
    sns.set_topic_attributes(TopicArn=feedback,AttributeName='Policy',AttributeValue=json.dumps(feedback_policy))
    configuration='vox-photoshoot'
    try:ses.create_configuration_set(ConfigurationSetName=configuration)
    except ses.exceptions.AlreadyExistsException:pass
    destination={'Enabled':True,'MatchingEventTypes':['DELIVERY','BOUNCE','COMPLAINT'],'SnsDestination':{'TopicArn':feedback}}
    try:ses.create_configuration_set_event_destination(ConfigurationSetName=configuration,EventDestinationName='feedback',EventDestination=destination)
    except ses.exceptions.AlreadyExistsException:ses.update_configuration_set_event_destination(ConfigurationSetName=configuration,EventDestinationName='feedback',EventDestination=destination)
    role_name='VoxPhotoshootNotificationWorker'
    try:role=iam.get_role(RoleName=role_name)['Role']['Arn']
    except iam.exceptions.NoSuchEntityException:
        role=iam.create_role(RoleName=role_name,AssumeRolePolicyDocument=json.dumps({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'lambda.amazonaws.com'},'Action':'sts:AssumeRole'}]}))['Role']['Arn']
    policy={'Version':'2012-10-17','Statement':[
        {'Effect':'Allow','Action':['secretsmanager:GetSecretValue'],'Resource':worker_arn},
        {'Effect':'Allow','Action':['ses:SendEmail'],'Resource':[
            f'arn:aws:ses:{REGION}:{ACCOUNT}:identity/voxdonna.com',
            f'arn:aws:ses:{REGION}:{ACCOUNT}:configuration-set/vox-photoshoot']},
        {'Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],'Resource':f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/{NAME}:*'},
        {'Effect':'Allow','Action':['cloudwatch:PutMetricData'],'Resource':'*','Condition':{'StringEquals':{'cloudwatch:namespace':'Donna/Photoshoot'}}},
        {'Effect':'Allow','Action':['ec2:CreateNetworkInterface','ec2:DescribeNetworkInterfaces','ec2:DeleteNetworkInterface','ec2:DescribeSubnets','ec2:AssignPrivateIpAddresses','ec2:UnassignPrivateIpAddresses'],'Resource':'*'}]}
    iam.put_role_policy(RoleName=role_name,PolicyName='PhotoshootNotifications',PolicyDocument=json.dumps(policy))
    iam.put_role_policy(RoleName='VoxPhotoshootInstanceRole',PolicyName='AdministratorAuthenticatorEncryption',PolicyDocument=json.dumps({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Action':['kms:Encrypt','kms:Decrypt'],'Resource':key_arn,'Condition':{'StringEquals':{'kms:EncryptionContext:application':'vox-photoshoot-admin'}}}]}))
    try:logs.create_log_group(logGroupName='/aws/lambda/'+NAME)
    except logs.exceptions.ResourceAlreadyExistsException:pass
    logs.put_retention_policy(logGroupName='/aws/lambda/'+NAME,retentionInDays=14)
    env={'NOTIFICATION_SECRET_ARN':worker_arn,'NOTIFICATION_TOPIC_ARN':feedback,
         'NOTIFICATION_REGION':REGION,'NOTIFICATION_FROM':'notifications@voxdonna.com',
         'NOTIFICATION_REPLY_TO':'suyash@voxdonna.com','NOTIFICATION_CONFIGURATION_SET':configuration,
         'PUBLIC_ORIGIN':'https://photo.voxdonna.com','NOTIFICATION_PACE':'1'}
    config={'FunctionName':NAME,'Role':role,'Handler':'notification_worker.handler','Timeout':90,'MemorySize':256,
            'Environment':{'Variables':env},'VpcConfig':{'SubnetIds':network['subnets'],'SecurityGroupIds':[network['app_security_group']]}}
    package=pathlib.Path(zip_path).read_bytes()
    try:
        function=lam.get_function(FunctionName=NAME)['Configuration']['FunctionArn']
        lam.update_function_code(FunctionName=NAME,ZipFile=package)
        lam.get_waiter('function_updated_v2').wait(FunctionName=NAME)
        lam.update_function_configuration(**config)
    except lam.exceptions.ResourceNotFoundException:
        # Role propagation is eventually consistent. A rerun is safe if AWS needs more time.
        function=lam.create_function(**config,Runtime='python3.13',Architectures=['x86_64'],Code={'ZipFile':package})['FunctionArn']
    lam.put_function_concurrency(FunctionName=NAME,ReservedConcurrentExecutions=2)
    for statement,principal,source in [('ses-feedback','sns.amazonaws.com',feedback),('scheduled-dispatch','events.amazonaws.com',f'arn:aws:events:{REGION}:{ACCOUNT}:rule/vox-photoshoot-notifications')]:
        try:lam.add_permission(FunctionName=NAME,StatementId=statement,Action='lambda:InvokeFunction',Principal=principal,SourceArn=source,SourceAccount=ACCOUNT)
        except lam.exceptions.ResourceConflictException:pass
    sns.subscribe(TopicArn=feedback,Protocol='lambda',Endpoint=function)
    existing=sns.list_subscriptions_by_topic(TopicArn=alerts)['Subscriptions']
    if not any(s['Protocol']=='email' and s['Endpoint']=='suyash@voxdonna.com' for s in existing):
        sns.subscribe(TopicArn=alerts,Protocol='email',Endpoint='suyash@voxdonna.com')
    events.put_rule(Name='vox-photoshoot-notifications',ScheduleExpression='rate(1 minute)',State='DISABLED')
    events.put_targets(Rule='vox-photoshoot-notifications',Targets=[{'Id':'worker','Arn':function}])
    connector=boto3.client('apprunner',region_name=REGION)
    connectors=connector.list_vpc_connectors()['VpcConnectors']
    found=next((c for c in connectors if c['VpcConnectorName']=='vox-photoshoot-private' and c['Status']=='ACTIVE'),None)
    if not found:
        found=connector.create_vpc_connector(VpcConnectorName='vox-photoshoot-private',Subnets=network['subnets'],SecurityGroups=[network['app_security_group']])['VpcConnector']
    ec=boto3.client('ec2',region_name=REGION)
    permissions=ec.describe_security_groups(GroupIds=[network['database_security_group']])['SecurityGroups'][0]['IpPermissions']
    if not any(p.get('FromPort')==5432 and any(g['GroupId']==network['app_security_group'] for g in p.get('UserIdGroupPairs',[])) for p in permissions):
        ec.authorize_security_group_ingress(GroupId=network['database_security_group'],IpPermissions=[{'IpProtocol':'tcp','FromPort':5432,'ToPort':5432,'UserIdGroupPairs':[{'GroupId':network['app_security_group'],'Description':'Photoshoot private app and worker'}]}])
    result={'key_arn':key_arn,'app_secret_arn':app['ARN'],'worker_secret_arn':worker_arn,'function':function,
            'feedback_topic':feedback,'alert_topic':alerts,'vpc_connector':found['VpcConnectorArn']}
    out=pathlib.Path('/private/tmp/vox-admin-delivery.json');out.write_text(json.dumps(result,indent=2));assert json.loads(out.read_text())==result
    print(json.dumps(result),flush=True)


if __name__=='__main__':main(sys.argv[1])
