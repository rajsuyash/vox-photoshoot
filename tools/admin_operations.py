"""Native Photoshoot alarms. Deploy after the disabled monitor worker is prepared."""
import json
from pathlib import Path

import boto3


def main():
    state = json.loads(Path('/private/tmp/vox-admin-delivery.json').read_text())
    cw = boto3.client('cloudwatch', region_name='ap-south-1')
    logs = boto3.client('logs', region_name='ap-south-1')
    group = '/aws/apprunner/vox-photoshoot/74c51f50e3014e2587ea8fa9caed99f0/application'
    logs.put_metric_filter(logGroupName=group, filterName='PaymentWebhookFailures',
                           filterPattern='"razorpay webhook failed"',
                           metricTransformations=[{'metricName':'PaymentWebhookFailures',
                               'metricNamespace':'Donna/Photoshoot', 'metricValue':'1', 'defaultValue':0}])
    app_dimensions = [{'Name':'ServiceName','Value':'vox-photoshoot'},
                      {'Name':'ServiceID','Value':'74c51f50e3014e2587ea8fa9caed99f0'}]
    alarms = [
        ('API errors', 'AWS/AppRunner', '5xxStatusResponses', 5, app_dimensions, 'Sum'),
        ('Payment webhooks', 'Donna/Photoshoot', 'PaymentWebhookFailures', 1, [], 'Sum'),
        ('Email age', 'Donna/Photoshoot', 'EmailQueueAge', 600, [], 'Maximum'),
        ('Email failures', 'Donna/Photoshoot', 'FailedEmails', 1, [], 'Maximum'),
        ('Stalled jobs', 'Donna/Photoshoot', 'StalledJobs', 1, [], 'Maximum'),
        # Credit reservations are a usage signal, not measured provider currency spend.
        ('Daily credit reservations', 'Donna/Photoshoot', 'DailyCreditsReserved', 1000, [], 'Maximum'),
        ('Worker errors', 'AWS/Lambda', 'Errors', 1,
         [{'Name':'FunctionName','Value':'vox-photoshoot-notifications'}], 'Sum'),
        ('Worker throttling', 'AWS/Lambda', 'Throttles', 1,
         [{'Name':'FunctionName','Value':'vox-photoshoot-notifications'}], 'Sum'),
    ]
    for name, namespace, metric, threshold, dimensions, statistic in alarms:
        cw.put_metric_alarm(AlarmName='Photoshoot '+name, Namespace=namespace, MetricName=metric,
                            Dimensions=dimensions, Statistic=statistic, Period=60,
                            EvaluationPeriods=1, Threshold=threshold, ComparisonOperator='GreaterThanOrEqualToThreshold',
                            TreatMissingData='notBreaching', AlarmActions=[state['alert_topic']],
                            AlarmDescription='Donna Photoshoot support: '+name)
    print('configured', len(alarms), 'native alarms')


if __name__ == '__main__':
    main()
