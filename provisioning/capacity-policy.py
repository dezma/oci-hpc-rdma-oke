#!/usr/bin/env python3
"""Render opt-in capacity IAM requests for an existing operator; never submit them."""
import argparse
import json
from pathlib import Path
import re

p=argparse.ArgumentParser(description=__doc__)
for key,kind in [('stack-id','ormstack'),('node-pool-id','nodepool'),('dynamic-group-id','dynamicgroup'),('compartment-id','compartment'),('tenancy-id','tenancy')]:
    p.add_argument('--'+key,required=True)
p.add_argument('--name-prefix',default='oke-rdma')
p.add_argument('--output-dir',default='capacity-policy')
a=p.parse_args()
for key,kind in [('stack_id','ormstack'),('node_pool_id','nodepool'),('dynamic_group_id','dynamicgroup'),('compartment_id','compartment'),('tenancy_id','tenancy')]:
    if not re.fullmatch(r'ocid1[.]'+kind+r'[.][A-Za-z0-9._-]+',getattr(a,key)):p.error('Invalid '+key)
if not re.fullmatch(r'[a-z][a-z0-9-]{2,23}',a.name_prefix):p.error('Use a lowercase environment name of 3–24 characters')
prefix='Allow dynamic-group id '+a.dynamic_group_id+' to '
where=' in compartment id '+a.compartment_id
statements=[
 prefix+'{ORM_STACK_READ, ORM_STACK_USE, ORM_STACK_UPDATE}'+where,
 prefix+'read orm-jobs'+where+" where target.stack.id = '"+a.stack_id+"'",
 prefix+'manage orm-jobs'+where+" where all {target.stack.id = '"+a.stack_id+"', any {target.job.operation = 'PLAN', target.job.operation = 'APPLY'}}",
 prefix+'read cluster-node-pools'+where+" where target.nodepool.id = '"+a.node_pool_id+"'",
 prefix+'{CLUSTER_NODE_POOL_UPDATE}'+where+" where target.nodepool.id = '"+a.node_pool_id+"'",
 prefix+'manage cluster-node-pools'+where+" where all {target.nodepool.id = '"+a.node_pool_id+"', request.operation = 'DeleteNode'}",
 prefix+'read cluster-work-requests'+where,
 prefix+'manage instance-family'+where,
]+[prefix+verb+' '+resource+where for verb,resource in [('read','virtual-network-family'),('use','subnets'),('use','vnics'),('use','private-ips'),('use','network-security-groups'),('read','compute-clusters'),('read','buckets'),('read','bastion-family'),('read','policies')]]
tenancy=[prefix+'manage compute-capacity-reports in tenancy']+[prefix+verb+' '+resource+' in tenancy' for verb,resource in [('read','dynamic-groups'),('read','policies'),('inspect','compartments'),('inspect','availability-domains'),('read','objectstorage-namespaces')]]
out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
for filename,compartment,name,rules in [('compartment.json',a.compartment_id,a.name_prefix+'-capacity-ui',statements),('tenancy.json',a.tenancy_id,a.name_prefix+'-capacity-ui-read',tenancy)]:
    path=out/filename
    if path.exists():p.error('Refusing to overwrite '+str(path))
    path.write_text(json.dumps({'compartmentId':compartment,'name':name,'description':'Reviewed Control Center capacity management permissions','statements':rules},indent=2)+'\n')
    print(path)
