#!/bin/bash
aws ec2 disassociate-address --association-id
aws ec2 release-address --allocation-id
aws ec2 terminate-instances --instance-ids i-09a04733993472259
aws ec2 wait instance-terminated --instance-ids i-09a04733993472259
aws ec2 delete-security-group --group-id sg-1257816c
aws ec2 disassociate-route-table --association-id rtbassoc-3fd17844
aws ec2 delete-route-table --route-table-id rtb-b52fc3cd
aws ec2 detach-internet-gateway --internet-gateway-id igw-6e2ff108 --vpc-id vpc-21b45458
aws ec2 delete-internet-gateway --internet-gateway-id igw-6e2ff108
aws ec2 delete-subnet --subnet-id subnet-14470f28
aws ec2 delete-vpc --vpc-id vpc-21b45458
echo If you want to delete the key-pair, please do it manually.
