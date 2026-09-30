"""Prepare private Photoshoot egress and an isolated restore. Does not switch App Runner.

Run with the existing AWS profile. Stores resource IDs/evidence, never credentials.
One NAT is intentional at this scale; use one per AZ if availability requirements grow.
"""
import json
import pathlib
import urllib.request

import boto3

REGION = 'ap-south-1'
VPC = 'vpc-0de5f935e53b90183'
DB = 'vox-photoshoot-db'
STATE = pathlib.Path('/private/tmp/vox-admin-network.json')


def main():
    ec = boto3.client('ec2', region_name=REGION)
    rds = boto3.client('rds', region_name=REGION)
    def tags(name):
        return [{'Key': 'Name', 'Value': name}, {'Key': 'Application', 'Value': 'vox-photoshoot'}]
    def security_group(name):
        rows = ec.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [VPC]},
                                                   {'Name': 'group-name', 'Values': [name]}])['SecurityGroups']
        if rows:
            return rows[0]['GroupId']
        ident = ec.create_security_group(GroupName=name, Description=name, VpcId=VPC)['GroupId']
        ec.create_tags(Resources=[ident], Tags=tags(name))
        return ident
    app_sg = security_group('vox-photoshoot-private-egress')
    restore_sg = security_group('vox-photoshoot-restore-test')
    with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=15) as response:
        address = response.read().decode().strip() + '/32'
    ingress = ec.describe_security_groups(GroupIds=[restore_sg])['SecurityGroups'][0]['IpPermissions']
    if not any(address == r['CidrIp'] for p in ingress for r in p.get('IpRanges', [])):
        ec.authorize_security_group_ingress(GroupId=restore_sg, IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 5432,
                         'ToPort': 5432, 'IpRanges': [{'CidrIp': address, 'Description': 'temporary restore verification'}]}])
    private = []
    for cidr, az in [('172.31.96.0/24','ap-south-1a'), ('172.31.97.0/24','ap-south-1b')]:
        existing = ec.describe_subnets(Filters=[{'Name':'vpc-id','Values':[VPC]}, {'Name':'cidr-block','Values':[cidr]}])['Subnets']
        if existing:
            ident = existing[0]['SubnetId']
            assert any(t['Key']=='Application' and t['Value']=='vox-photoshoot' for t in existing[0].get('Tags',[]))
        else:
            ident = ec.create_subnet(VpcId=VPC,CidrBlock=cidr,AvailabilityZone=az)['Subnet']['SubnetId']
            ec.create_tags(Resources=[ident],Tags=tags('vox-photoshoot-private-'+az))
        private.append(ident)
    nat = ec.describe_nat_gateways(Filter=[{'Name':'tag:Name','Values':['vox-photoshoot-nat']},
                                          {'Name':'state','Values':['pending','available']}])['NatGateways']
    if nat:
        nat_id = nat[0]['NatGatewayId']
    else:
        allocated = ec.describe_addresses(Filters=[{'Name':'tag:Name','Values':['vox-photoshoot-nat']}])['Addresses']
        if allocated:
            allocation = allocated[0]['AllocationId']
        else:
            allocation = ec.allocate_address(Domain='vpc')['AllocationId']
            ec.create_tags(Resources=[allocation],Tags=tags('vox-photoshoot-nat'))
        nat_id = ec.create_nat_gateway(SubnetId='subnet-017e85a4160c887b3',AllocationId=allocation,
                                       TagSpecifications=[{'ResourceType':'natgateway','Tags':tags('vox-photoshoot-nat')}])['NatGateway']['NatGatewayId']
    tables = ec.describe_route_tables(Filters=[{'Name':'tag:Name','Values':['vox-photoshoot-private']}])['RouteTables']
    if tables:
        route_id = tables[0]['RouteTableId']
    else:
        route_id = ec.create_route_table(VpcId=VPC)['RouteTable']['RouteTableId']
        ec.create_tags(Resources=[route_id],Tags=tags('vox-photoshoot-private'))
    routes = ec.describe_route_tables(RouteTableIds=[route_id])['RouteTables'][0]
    if not any(r.get('DestinationCidrBlock')=='0.0.0.0/0' for r in routes['Routes']):
        ec.get_waiter('nat_gateway_available').wait(NatGatewayIds=[nat_id],
                                                   WaiterConfig={'Delay':10,'MaxAttempts':40})
        ec.create_route(RouteTableId=route_id,DestinationCidrBlock='0.0.0.0/0',NatGatewayId=nat_id)
    for subnet in private:
        if not any(a.get('SubnetId')==subnet for a in routes['Associations']):
            ec.associate_route_table(RouteTableId=route_id,SubnetId=subnet)
    current = rds.describe_db_instances(DBInstanceIdentifier=DB)['DBInstances'][0]
    restored = rds.describe_db_instances(Filters=[{'Name':'db-instance-id','Values':['vox-admin-restore-20260930']}])['DBInstances']
    cutoff = (json.loads(STATE.read_text())['restore_time'] if STATE.exists()
              else current['LatestRestorableTime'].isoformat())
    if not restored:
        from datetime import datetime
        rds.restore_db_instance_to_point_in_time(SourceDBInstanceIdentifier=DB,
            TargetDBInstanceIdentifier='vox-admin-restore-20260930',RestoreTime=datetime.fromisoformat(cutoff),
            DBInstanceClass='db.t4g.micro',PubliclyAccessible=True,MultiAZ=False,
            VpcSecurityGroupIds=[restore_sg],Tags=tags('vox-photoshoot-restore-test'))
    result = {'region':REGION,'subnets':private,'app_security_group':app_sg,'nat_gateway':nat_id,
              'restore_security_group':restore_sg,'restore_instance':'vox-admin-restore-20260930',
              'restore_time':cutoff,'database_security_group':'sg-01d5be2d119020e4f'}
    STATE.write_text(json.dumps(result,indent=2));assert json.loads(STATE.read_text())==result
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()
