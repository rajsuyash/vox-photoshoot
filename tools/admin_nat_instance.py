"""Replace NAT gateway `nat-092ef3b01ff293f14` with a t4g.nano NAT instance.

See docs/plan-2026-10-02-nat-instance.md. `prepare` never touches routing — it only
builds the instance, EIP and alarms, then waits for it to report ready. `cutover` and
`rollback` are the only subcommands that call ReplaceRoute, and only ever on
rtb-03927d2197bb437fa. `cleanup` refuses unless that route already targets the
instance. `teardown` undoes a failed `prepare` (terminates the instance, releases its
EIP, deletes its alarms; keeps the reusable SG and IAM role) so prepare can be re-run —
it refuses if the route table currently targets the instance. Never prints credentials.
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

REGION = 'ap-south-1'
VPC = 'vpc-0de5f935e53b90183'
ROUTE_TABLE = 'rtb-03927d2197bb437fa'
NAT_GATEWAY = 'nat-092ef3b01ff293f14'
NAT_GATEWAY_EIP_TAG = 'vox-photoshoot-nat'
APP_SG = 'sg-0720f81877b10330c'
SUBNET = 'subnet-017e85a4160c887b3'
INSTANCE_TAG = 'vox-photoshoot-nat-instance'
SG_NAME = 'vox-photoshoot-nat-instance'
ROLE_NAME = 'VoxPhotoshootNatInstance'
HEALTHZ_URL = 'https://photo.voxdonna.com/healthz'
NAT_INSTANCE_ALARMS = ('vox-photoshoot-nat-system-check', 'vox-photoshoot-nat-instance-check')
NOTIFICATIONS_FN = 'vox-photoshoot-notifications'
DURATION_LIMIT_MS = 80000  # Lambda timeout is 90000ms; this is the warn-early margin
POST_CUTOVER_TIMEOUT = 360  # ~6 min: CloudWatch Lambda metrics can lag 1-3 min
POST_CUTOVER_POLL = 30
STATE = pathlib.Path('/private/tmp/vox-admin-nat-instance.json')

USER_DATA = """#!/bin/bash
set -euo pipefail
trap 'echo "VOXNAT-FAILED at line $LINENO: $BASH_COMMAND" > /dev/console' ERR

# t4g.nano has 418 MB RAM; dnf installing iptables-services gets OOM-killed without this.
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile
  mkswap /swapfile
fi
swapon --show | grep -q /swapfile || swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/90-vox-nat.conf
sysctl -p /etc/sysctl.d/90-vox-nat.conf

dnf install -y --setopt=install_weak_deps=False iptables-services

IFACE=$(ip route show default | awk '{print $5; exit}')

iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE
iptables -A FORWARD -j ACCEPT

service iptables save
systemctl enable --now iptables

echo "VOXNAT-READY forward=$(sysctl -n net.ipv4.ip_forward) masq=$(iptables -t nat -S POSTROUTING | grep -c MASQUERADE) swap=$(swapon --show --noheadings | wc -l)" > /dev/console
"""


def tags(name):
    return [{'Key': 'Name', 'Value': name}, {'Key': 'Application', 'Value': 'vox-photoshoot'}]


def find_instance(ec):
    reservations = ec.describe_instances(Filters=[
        {'Name': 'tag:Name', 'Values': [INSTANCE_TAG]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ])['Reservations']
    for reservation in reservations:
        for instance in reservation['Instances']:
            return instance
    return None


def route_target(route):
    for key in ('NatGatewayId', 'InstanceId', 'GatewayId', 'TransitGatewayId',
                'NetworkInterfaceId', 'VpcPeeringConnectionId'):
        if route and route.get(key):
            return key, route[key]
    return None, None


def default_route(ec):
    table = ec.describe_route_tables(RouteTableIds=[ROUTE_TABLE])['RouteTables'][0]
    route = next((r for r in table['Routes'] if r.get('DestinationCidrBlock') == '0.0.0.0/0'), None)
    return table, route


def console_text(ec, instance_id):
    # GetConsoleOutput lags (buffered, sometimes several minutes old) and 'Output' can be
    # absent or None before the instance has produced any output yet — callers must treat
    # an empty result as "keep polling", never as a verdict.
    #
    # botocore already base64-decodes 'Output' for this operation — it comes back as plain
    # text (verified 2026-10-02 against a live instance: readable kernel/cloud-init log,
    # including the literal "VOXNAT-FAILED at line 8: dnf install..." line). Decoding it
    # again here turned that same text into garbage bytes and silently broke every
    # VOXNAT-FAILED/VOXNAT-READY string match — this was the actual reason `prepare` ran
    # out the full 600s timeout instead of exiting the moment the console showed failure.
    return ec.get_console_output(InstanceId=instance_id, Latest=True).get('Output') or ''


def find_alert_topic(sns):
    paginator = sns.get_paginator('list_topics')
    for page in paginator.paginate():
        for topic in page['Topics']:
            if topic['TopicArn'].endswith(':vox-photoshoot-alerts'):
                return topic['TopicArn']
    raise SystemExit('refusing: SNS topic vox-photoshoot-alerts not found — run admin_delivery.py first')


# --- status (read-only) ------------------------------------------------------

def cmd_status(args):
    ec = boto3.client('ec2', region_name=REGION)
    cw = boto3.client('cloudwatch', region_name=REGION)
    table, route = default_route(ec)
    target_kind, target_id = route_target(route)
    nat = ec.describe_nat_gateways(NatGatewayIds=[NAT_GATEWAY])['NatGateways'][0]
    instance = find_instance(ec)
    instance_info = None
    if instance:
        instance_id = instance['InstanceId']
        status_rows = ec.describe_instance_status(InstanceIds=[instance_id], IncludeAllInstances=True)['InstanceStatuses']
        status = status_rows[0] if status_rows else {}
        instance_info = {
            'instance_id': instance_id,
            'state': instance['State']['Name'],
            'system_status': status.get('SystemStatus', {}).get('Status'),
            'instance_status': status.get('InstanceStatus', {}).get('Status'),
            'source_dest_check': instance.get('SourceDestCheck'),
            'public_ip': instance.get('PublicIpAddress'),
        }
    alarms = {}
    for name in NAT_INSTANCE_ALARMS:
        rows = cw.describe_alarms(AlarmNames=[name])['MetricAlarms']
        alarms[name] = rows[0]['StateValue'] if rows else None
    result = {
        'route_table': ROUTE_TABLE,
        'route_table_is_main': any(a.get('Main') for a in table['Associations']),
        'default_route_target_kind': target_kind,
        'default_route_target_id': target_id,
        'nat_gateway_state': nat['State'],
        'nat_instance': instance_info,
        'alarms': alarms,
    }
    print(json.dumps(result, indent=2))


# --- prepare ------------------------------------------------------------------

def ensure_security_group(ec):
    rows = ec.describe_security_groups(Filters=[
        {'Name': 'vpc-id', 'Values': [VPC]}, {'Name': 'group-name', 'Values': [SG_NAME]},
    ])['SecurityGroups']
    if rows:
        sg_id, permissions = rows[0]['GroupId'], rows[0]['IpPermissions']
    else:
        sg_id = ec.create_security_group(GroupName=SG_NAME, Description='NAT instance ingress from app SG',
                                          VpcId=VPC)['GroupId']
        ec.create_tags(Resources=[sg_id], Tags=tags(SG_NAME))
        permissions = []
    has_rule = any(p.get('IpProtocol') == '-1'
                   and any(g['GroupId'] == APP_SG for g in p.get('UserIdGroupPairs', [])) for p in permissions)
    if not has_rule:
        ec.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            'IpProtocol': '-1', 'UserIdGroupPairs': [{'GroupId': APP_SG, 'Description': 'app/worker egress via NAT instance'}],
        }])
    return sg_id


def ensure_iam_profile(iam):
    try:
        iam.get_role(RoleName=ROLE_NAME)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps({
            'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
        }))
    attached = iam.list_attached_role_policies(RoleName=ROLE_NAME)['AttachedPolicies']
    if not any(p['PolicyName'] == 'AmazonSSMManagedInstanceCore' for p in attached):
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
    try:
        iam.get_instance_profile(InstanceProfileName=ROLE_NAME)
    except iam.exceptions.NoSuchEntityException:
        iam.create_instance_profile(InstanceProfileName=ROLE_NAME)
    profile = iam.get_instance_profile(InstanceProfileName=ROLE_NAME)['InstanceProfile']
    if not any(r['RoleName'] == ROLE_NAME for r in profile['Roles']):
        iam.add_role_to_instance_profile(InstanceProfileName=ROLE_NAME, RoleName=ROLE_NAME)
    return ROLE_NAME


def ensure_instance(ec, ssm, sg_id, profile_name):
    existing = find_instance(ec)
    if existing:
        if 'VOXNAT-FAILED' in console_text(ec, existing['InstanceId']):
            raise SystemExit('refusing: existing NAT instance user-data failed (VOXNAT-FAILED) — run teardown first')
        return existing['InstanceId']
    ami_id = ssm.get_parameter(
        Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-arm64')['Parameter']['Value']
    image = ec.describe_images(ImageIds=[ami_id])['Images'][0]
    root_device = image['RootDeviceName']
    root_ebs = next(m['Ebs'] for m in image['BlockDeviceMappings'] if m['DeviceName'] == root_device)
    root_ebs.update({'Encrypted': True, 'VolumeType': 'gp3'})
    root_ebs.pop('Iops', None)
    root_ebs.pop('Throughput', None)

    # IAM instance profile propagation is eventually consistent after creation.
    deadline = time.time() + 60
    reservation = None
    while time.time() < deadline:
        try:
            reservation = ec.run_instances(
                ImageId=ami_id, InstanceType='t4g.nano', MinCount=1, MaxCount=1,
                SubnetId=SUBNET, SecurityGroupIds=[sg_id],
                IamInstanceProfile={'Name': profile_name},
                MetadataOptions={'HttpTokens': 'required', 'HttpEndpoint': 'enabled'},
                BlockDeviceMappings=[{'DeviceName': root_device, 'Ebs': root_ebs}],
                UserData=USER_DATA,
                TagSpecifications=[{'ResourceType': 'instance', 'Tags': tags(INSTANCE_TAG)}])
            break
        except ClientError as exc:
            if 'Invalid IAM Instance Profile' not in str(exc):
                raise
            time.sleep(5)
    if reservation is None:
        raise SystemExit('refusing: IAM instance profile never became usable within 60s')
    instance_id = reservation['Instances'][0]['InstanceId']
    ec.get_waiter('instance_running').wait(InstanceIds=[instance_id])
    ec.modify_instance_attribute(InstanceId=instance_id, SourceDestCheck={'Value': False})
    return instance_id


def ensure_eip(ec, instance_id):
    rows = ec.describe_addresses(Filters=[{'Name': 'tag:Name', 'Values': [INSTANCE_TAG]}])['Addresses']
    if rows:
        address = rows[0]
    else:
        allocation = ec.allocate_address(Domain='vpc')
        ec.create_tags(Resources=[allocation['AllocationId']], Tags=tags(INSTANCE_TAG))
        address = ec.describe_addresses(AllocationIds=[allocation['AllocationId']])['Addresses'][0]
    if address.get('InstanceId') != instance_id:
        ec.associate_address(AllocationId=address['AllocationId'], InstanceId=instance_id)
        address = ec.describe_addresses(AllocationIds=[address['AllocationId']])['Addresses'][0]
    return address['PublicIp']


def ensure_alarms(cw, instance_id, alert_topic):
    system_check, instance_check = NAT_INSTANCE_ALARMS
    cw.put_metric_alarm(
        AlarmName=system_check, Namespace='AWS/EC2', MetricName='StatusCheckFailed_System',
        Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}], Statistic='Maximum', Period=60,
        EvaluationPeriods=2, Threshold=1, ComparisonOperator='GreaterThanOrEqualToThreshold',
        TreatMissingData='notBreaching', AlarmActions=['arn:aws:automate:ap-south-1:ec2:recover', alert_topic],
        AlarmDescription='Donna Photoshoot NAT instance: system status check failed')
    cw.put_metric_alarm(
        AlarmName=instance_check, Namespace='AWS/EC2', MetricName='StatusCheckFailed_Instance',
        Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}], Statistic='Maximum', Period=60,
        EvaluationPeriods=2, Threshold=1, ComparisonOperator='GreaterThanOrEqualToThreshold',
        TreatMissingData='notBreaching', AlarmActions=['arn:aws:automate:ap-south-1:ec2:reboot', alert_topic],
        AlarmDescription='Donna Photoshoot NAT instance: instance status check failed')


def wait_ready(ec, instance_id, timeout=600):
    deadline = time.time() + timeout
    seen_ready = False
    checks_ok = False
    text = ''
    while time.time() < deadline:
        text = console_text(ec, instance_id)
        if 'VOXNAT-FAILED' in text:
            raise SystemExit('refusing: user-data reported VOXNAT-FAILED:\n' + text[-2000:])
        seen_ready = 'VOXNAT-READY' in text
        status_rows = ec.describe_instance_status(InstanceIds=[instance_id], IncludeAllInstances=True)['InstanceStatuses']
        if status_rows:
            status = status_rows[0]
            checks_ok = (status['SystemStatus']['Status'] == 'ok' and status['InstanceStatus']['Status'] == 'ok')
        if seen_ready and checks_ok:
            return text
        time.sleep(15)
    raise SystemExit(f'refusing: timed out after {timeout}s waiting for 2/2 checks + VOXNAT-READY '
                      f'(seen_ready={seen_ready}, checks_ok={checks_ok})')


def cmd_prepare(args):
    ec = boto3.client('ec2', region_name=REGION)
    ssm = boto3.client('ssm', region_name=REGION)
    iam = boto3.client('iam')
    cw = boto3.client('cloudwatch', region_name=REGION)
    sns = boto3.client('sns', region_name=REGION)

    sg_id = ensure_security_group(ec)
    profile_name = ensure_iam_profile(iam)
    instance_id = ensure_instance(ec, ssm, sg_id, profile_name)
    public_ip = ensure_eip(ec, instance_id)
    alert_topic = find_alert_topic(sns)
    ensure_alarms(cw, instance_id, alert_topic)
    console = wait_ready(ec, instance_id)

    result = {'instance_id': instance_id, 'security_group': sg_id, 'iam_profile': profile_name,
              'public_ip': public_ip, 'alert_topic': alert_topic, 'console_tail': console[-500:]}
    STATE.write_text(json.dumps(result, indent=2))
    assert json.loads(STATE.read_text()) == result
    print(json.dumps(result, indent=2))


# --- cutover / rollback / cleanup ---------------------------------------------

def _parse_ready(text):
    match = re.search(r'VOXNAT-READY forward=(\d+) masq=(\d+)', text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _network_out_sum(cw, instance_id):
    now = datetime.now(timezone.utc)
    points = cw.get_metric_statistics(
        Namespace='AWS/EC2', MetricName='NetworkOut', Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
        StartTime=now - timedelta(minutes=10), EndTime=now, Period=300, Statistics=['Sum'])['Datapoints']
    return sum(p['Sum'] for p in points)


def _lambda_metrics_since(cw, start):
    """Invocations/Errors/max Duration for NOTIFICATIONS_FN from `start` to now.

    This function sits in the same private subnets and runs every minute via EventBridge;
    its CloudWatch/Secrets Manager calls need NAT egress (no interface endpoints exist in
    this VPC — verified separately). healthz makes no outbound call and proves nothing
    about egress, so this is the real signal cutover depends on.
    """
    common = dict(Namespace='AWS/Lambda', Dimensions=[{'Name': 'FunctionName', 'Value': NOTIFICATIONS_FN}],
                  StartTime=start, EndTime=datetime.now(timezone.utc), Period=60)
    invocations = cw.get_metric_statistics(MetricName='Invocations', Statistics=['Sum'], **common)['Datapoints']
    errors = cw.get_metric_statistics(MetricName='Errors', Statistics=['Sum'], **common)['Datapoints']
    duration = cw.get_metric_statistics(MetricName='Duration', Statistics=['Maximum'], **common)['Datapoints']
    invocation_count = sum(p['Sum'] for p in invocations)
    error_count = sum(p['Sum'] for p in errors)
    max_duration = max((p['Maximum'] for p in duration), default=None)
    return invocation_count, error_count, max_duration


def cmd_cutover(args):
    ec = boto3.client('ec2', region_name=REGION)
    instance = find_instance(ec)
    if not instance or instance['State']['Name'] != 'running':
        raise SystemExit('refusing: NAT instance is not running — run prepare first')
    instance_id = instance['InstanceId']

    status_rows = ec.describe_instance_status(InstanceIds=[instance_id])['InstanceStatuses']
    if not status_rows or status_rows[0]['SystemStatus']['Status'] != 'ok' \
            or status_rows[0]['InstanceStatus']['Status'] != 'ok':
        raise SystemExit('refusing: instance is not 2/2 status checks ok')
    if instance.get('SourceDestCheck') is not False:
        raise SystemExit('refusing: SourceDestCheck is not disabled on the instance')
    forward, masq = _parse_ready(console_text(ec, instance_id))
    if forward != 1 or not masq or masq < 1:
        raise SystemExit(f'refusing: console does not show VOXNAT-READY forward=1 masq>=1 '
                          f'(forward={forward}, masq={masq})')

    table, route = default_route(ec)
    assert table['RouteTableId'] == ROUTE_TABLE
    if any(a.get('Main') for a in table['Associations']):
        raise SystemExit('refusing: route table is the VPC main route table')
    previous_kind, previous_id = route_target(route)

    state = {'route_table': ROUTE_TABLE, 'previous_target_kind': previous_kind,
             'previous_target_id': previous_id, 'instance_id': instance_id}
    STATE.write_text(json.dumps(state, indent=2))
    assert json.loads(STATE.read_text()) == state

    cw = boto3.client('cloudwatch', region_name=REGION)
    first_network_out = _network_out_sum(cw, instance_id)
    cutover_time = datetime.now(timezone.utc)
    ec.replace_route(RouteTableId=ROUTE_TABLE, DestinationCidrBlock='0.0.0.0/0', InstanceId=instance_id)

    # Primary proof of egress: NOTIFICATIONS_FN invocations/errors since cutover_time (see
    # _lambda_metrics_since). healthz is secondary — it never makes an outbound call, so a
    # 200 there proves nothing about NAT working and cannot be the sole gate.
    deadline = time.time() + POST_CUTOVER_TIMEOUT
    route_active = False
    healthz_ok = False
    invocation_count = error_count = 0
    max_duration = None
    while time.time() < deadline:
        _, route = default_route(ec)
        route_active = bool(route and route.get('InstanceId') == instance_id and route.get('State') == 'active')
        try:
            with urllib.request.urlopen(HEALTHZ_URL, timeout=10) as response:
                healthz_ok = response.status == 200
        except (urllib.error.URLError, OSError):
            healthz_ok = False
        invocation_count, error_count, max_duration = _lambda_metrics_since(cw, cutover_time)
        lambda_pass = (invocation_count >= 2 and error_count == 0
                       and (max_duration is None or max_duration < DURATION_LIMIT_MS))
        if route_active and lambda_pass and healthz_ok:
            break
        if error_count > 0 or (max_duration is not None and max_duration >= DURATION_LIMIT_MS):
            break  # decisive failure — no point waiting out the rest of the window
        time.sleep(POST_CUTOVER_POLL)
    lambda_pass = (invocation_count >= 2 and error_count == 0
                   and (max_duration is None or max_duration < DURATION_LIMIT_MS))
    network_out_increasing = _network_out_sum(cw, instance_id) > first_network_out

    ok = route_active and lambda_pass and healthz_ok
    result = {
        'instance_id': instance_id,
        'route_active': route_active,
        'lambda_invocations_since_cutover': invocation_count,
        'lambda_errors_since_cutover': error_count,
        'lambda_max_duration_ms': max_duration,
        'healthz_ok': healthz_ok,
        'network_out_increasing_info_only': network_out_increasing,
    }
    if not ok:
        ec.replace_route(RouteTableId=ROUTE_TABLE, DestinationCidrBlock='0.0.0.0/0', NatGatewayId=NAT_GATEWAY)
        result['rolled_back_to'] = NAT_GATEWAY
        print(json.dumps(result, indent=2))
        raise SystemExit(1)
    print(json.dumps(result, indent=2))


def cmd_rollback(args):
    ec = boto3.client('ec2', region_name=REGION)
    nat = ec.describe_nat_gateways(NatGatewayIds=[NAT_GATEWAY])['NatGateways'][0]
    if nat['State'] != 'available':
        raise SystemExit(f'refusing: NAT gateway state is {nat["State"]!r}, not available')
    ec.replace_route(RouteTableId=ROUTE_TABLE, DestinationCidrBlock='0.0.0.0/0', NatGatewayId=NAT_GATEWAY)
    print(json.dumps({'rolled_back_to': NAT_GATEWAY}))


def cmd_cleanup(args):
    if args.confirm_delete_nat != NAT_GATEWAY:
        raise SystemExit(f'refusing: pass --confirm-delete-nat {NAT_GATEWAY} to confirm deletion')
    ec = boto3.client('ec2', region_name=REGION)
    instance = find_instance(ec)
    _, route = default_route(ec)
    if not instance or not route or route.get('InstanceId') != instance['InstanceId']:
        raise SystemExit('refusing: default route does not currently target the NAT instance')

    ec.delete_nat_gateway(NatGatewayId=args.confirm_delete_nat)
    ec.get_waiter('nat_gateway_deleted').wait(NatGatewayIds=[args.confirm_delete_nat],
                                               WaiterConfig={'Delay': 15, 'MaxAttempts': 40})
    addresses = ec.describe_addresses(Filters=[{'Name': 'tag:Name', 'Values': [NAT_GATEWAY_EIP_TAG]}])['Addresses']
    released = []
    for address in addresses:
        ec.release_address(AllocationId=address['AllocationId'])
        released.append(address['AllocationId'])
    print(json.dumps({'nat_gateway_deleted': args.confirm_delete_nat, 'eips_released': released}))


def cmd_teardown(args):
    ec = boto3.client('ec2', region_name=REGION)
    cw = boto3.client('cloudwatch', region_name=REGION)
    instance = find_instance(ec)
    if not instance:
        raise SystemExit('refusing: no NAT instance found with tag Name=' + INSTANCE_TAG)
    instance_id = instance['InstanceId']
    _, route = default_route(ec)
    if route and route.get('InstanceId') == instance_id:
        raise SystemExit(f'refusing: route table {ROUTE_TABLE} currently targets {instance_id} — roll back first')

    ec.terminate_instances(InstanceIds=[instance_id])
    ec.get_waiter('instance_terminated').wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 15, 'MaxAttempts': 40})

    addresses = ec.describe_addresses(Filters=[{'Name': 'tag:Name', 'Values': [INSTANCE_TAG]}])['Addresses']
    released = []
    for address in addresses:
        ec.release_address(AllocationId=address['AllocationId'])
        released.append(address['AllocationId'])

    cw.delete_alarms(AlarmNames=list(NAT_INSTANCE_ALARMS))  # no error if an alarm doesn't exist

    print(json.dumps({'terminated_instance': instance_id, 'eips_released': released,
                       'alarms_deleted': list(NAT_INSTANCE_ALARMS)}))


def build_parser():
    parser = argparse.ArgumentParser(
        description='Replace NAT gateway nat-092ef3b01ff293f14 with a t4g.nano NAT instance '
                    '(docs/plan-2026-10-02-nat-instance.md).')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Read-only: route target, NAT gateway state, NAT instance state, alarms.')
    sub.add_parser('prepare', help='Idempotently create the SG/role/instance/EIP/alarms. Never touches routing.')
    sub.add_parser('cutover', help='Precondition checks, then ReplaceRoute to the instance; auto-rollback if the '
                                    'notifications Lambda stalls/errors post-cutover (healthz is a secondary check).')
    sub.add_parser('rollback', help='ReplaceRoute back to the NAT gateway (refuses if it is not available).')
    cleanup = sub.add_parser('cleanup', help='Delete the NAT gateway and release its EIP. Requires --confirm-delete-nat.')
    cleanup.add_argument('--confirm-delete-nat', required=True,
                          help=f'Must equal {NAT_GATEWAY} to confirm deletion.')
    sub.add_parser('teardown', help='Terminate a (e.g. failed) NAT instance, release its EIP, delete its alarms. '
                                     'Keeps the SG and IAM role. Refuses if the route table targets the instance.')
    return parser


def main():
    args = build_parser().parse_args()
    commands = {'status': cmd_status, 'prepare': cmd_prepare, 'cutover': cmd_cutover,
                'rollback': cmd_rollback, 'cleanup': cmd_cleanup, 'teardown': cmd_teardown}
    commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
