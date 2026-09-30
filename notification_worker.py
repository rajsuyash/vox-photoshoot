"""Bounded scheduled SES dispatcher. Dispatch is disabled until provisioned/verified.

AWS IAM authenticates Lambda invocation. Provider feedback wiring is a separate
deployment step. No scheduler or public route is enabled by importing this file.
"""
import os
import json
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import notifications
import db


def ses_client():
    # SES has no SendEmail idempotency key: disable SDK retries on ambiguous sends.
    return boto3.client('sesv2', region_name=os.environ.get('NOTIFICATION_REGION', 'ap-south-1'),
                        config=Config(connect_timeout=5, read_timeout=15,
                                      retries={'total_max_attempts': 1}))


def dispatch(limit=10):
    if os.environ.get('NOTIFICATIONS_ENABLED') != '1':
        return {'disabled': True}
    sender = notifications.address(os.environ['NOTIFICATION_FROM'])
    reply = notifications.address(os.environ['NOTIFICATION_REPLY_TO'])
    configuration = os.environ['NOTIFICATION_CONFIGURATION_SET']
    if not configuration or not 1 <= limit <= 25:
        raise ValueError('invalid dispatcher configuration')
    client = ses_client()
    results = {'accepted': 0, 'retry': 0, 'failed': 0, 'stale': 0}
    for _ in range(limit):
        row = notifications.claim()
        if row is None:
            break
        try:
            rendered = notifications.render(row['kind'], row['payload'])
            body = {key: {'Data': rendered[field], 'Charset': 'UTF-8'}
                    for key, field in [('Text', 'text'), ('Html', 'html')]}
            response = client.send_email(
                FromEmailAddress=f'Donna Photoshoot <{sender}>',
                ReplyToAddresses=[reply], Destination={'ToAddresses': [row['recipient']]},
                ConfigurationSetName=configuration,
                EmailTags=[{'Name': 'notification_id', 'Value': str(row['id'])}],
                Content={'Simple': {'Subject': {'Data': rendered['subject'], 'Charset': 'UTF-8'},
                                    'Body': body}})
        except Exception as exc:
            code = exc.response['Error']['Code'] if isinstance(exc, ClientError) else type(exc).__name__
            state = notifications.failed(row, code, permanent=isinstance(exc, (ValueError, KeyError))
                                          or code in {'MessageRejected', 'MailFromDomainNotVerifiedException'})
            results['retry' if state == 'queued' else state] += 1
        else:
            recorded = notifications.accepted(row, response['MessageId'])
            results['accepted' if recorded else 'stale'] += 1
        if os.environ.get('NOTIFICATION_PACE') == '1':
            time.sleep(1.1)
    return results


def handler(event, context):
    if os.environ.get('NOTIFICATION_SECRET_ARN'):
        value = boto3.client('secretsmanager').get_secret_value(SecretId=os.environ['NOTIFICATION_SECRET_ARN'])
        secret = json.loads(value['SecretString'])
        if secret.get('DATABASE_URL') != os.environ.get('DATABASE_URL'):
            db.close()  # A warm worker must not keep connections to a previous secret version.
        for name in ('DATABASE_URL', 'ACCOUNT_LINK_KEY', 'NOTIFICATIONS_ENABLED', 'NOTIFICATIONS_SINCE'):
            if name in secret:
                os.environ[name] = secret[name]
    if 'Records' in event:
        for record in event['Records']:
            if record.get('EventSource') != 'aws:sns' or record['Sns']['TopicArn'] != os.environ['NOTIFICATION_TOPIC_ARN']:
                raise ValueError('unexpected feedback source')
            message = json.loads(record['Sns']['Message'])
            notifications.feedback(message['mail']['messageId'], message.get('eventType') or message.get('notificationType'),
                                   permanent=message.get('bounce', {}).get('bounceType') != 'Transient')
        return {'feedback': len(event['Records'])}
    result = dispatch()
    # Expired secrets are no longer deliverable. IDs/hashes in support history are safe.
    db.query("DELETE FROM recovery_attempts WHERE created_at<now()-interval '1 day'")
    metrics = db.query("SELECT count(*) FILTER(WHERE status='failed') AS failed,"
                       "COALESCE(extract(epoch FROM now()-min(created_at) FILTER(WHERE status IN ('queued','sending'))),0)::integer AS age "
                       "FROM notifications", one=True)
    stalled = db.query("SELECT count(*) AS n FROM jobs WHERE status IN ('queued','running') "
                       "AND COALESCE(heartbeat_at,created_at)<now()-interval '15 minutes'", one=True)['n']
    spending = db.query("SELECT COALESCE(-sum(delta),0) AS n FROM credit_ledger WHERE kind='reserve' "
                        "AND created_at>now()-interval '1 day'", one=True)['n']
    boto3.client('cloudwatch').put_metric_data(Namespace='Donna/Photoshoot', MetricData=[
        {'MetricName': name, 'Value': float(value)} for name, value in
        [('EmailQueueAge', metrics['age']), ('FailedEmails', metrics['failed']),
         ('StalledJobs', stalled), ('DailyCreditsReserved', spending)]])
    return result


if __name__ == '__main__':
    print(dispatch())
