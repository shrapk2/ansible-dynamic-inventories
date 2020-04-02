#!/usr/bin/env python3
import requests
import argparse
import json
import ipaddress
import urllib3
from api_secrets import ed_api_password, ed_api_username, snow_api_password
from ansible.plugins.inventory import BaseInventoryPlugin

# Note: Do get an Ansible deprecation warning since our group names are simply integers, here's a similar thread concerning dashes: https://github.com/ansible/ansible/issues/56930

# TODO: Add managed devices to inventory under host
# TODO: Add more grouping logic (region/location, any other?)
# TODO: Ansible Variable lookup or some other scalable logic to populate ansible vars

# FOR AWX/TOWER:
# Set shebang: #!/usr/bin/env python  (I use python3.7 so I know I'm testing/using Python3)
# Define passwords from mysecrets (or run cfg_localhost_dynamic_inventory_prep playbook)

##############################################
### SITE SPECIFIC VARS - UPDATE FOR SITE!!!###
##############################################
# SNOW API
snow_url = "https://service-now.example.com"
snow_headers = {'Authorization': snow_api_password}

# TODO: Add endpoint for each ED workflow [if needed]
# Secondary Inventory Source API Endpoints
ed_url_appliance = "https://secondary-inventory-source.example.com"
ed_url_manageddevs = "https://secondary-inventory-source.example.com"

# SITE SPECIFIC VARS
# These are for Ansible Inventory Host Facts and relative to the Ansible Control Node
site_ssh_user_account = 'ansible_svc_account'
site_ssh_private_key = '/path/to/svc_account/ssh.key'
site_ssh_args = '-o ProxyCommand =\"ssh - W % h: 22 - q bastion_svc_acct@bastion-host.example.com - i / etc/ansible/tmp/bastion_svc_acct/bastion_svc_acct.key\"'
##############################################
##############################################
##############################################

# Until valid HTTPS CA for Secondary Source
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# Set empty/default dictionary entries - user interaction not required
default_group_list = ['ungrouped']
client_groups = default_group_list + []
client_inventory = {}


# Cleans up a list for JSON
def set_default(obj):
    if isinstance(obj, set):
        return list(obj)
    raise TypeError


# Global function to clean up duplicate lists
def remove_dupes(obj):
    clean_list = []
    for client_id in obj:
        if client_id == "":
            continue
        elif client_id not in clean_list:
            clean_list.append(client_id)
        else:
            continue
    return clean_list


# Starting point of Python script, takes either --list or --host and goes crazy
def read_command_args():
    parser = argparse.ArgumentParser(
        description='Generates Ansible inventory from Service-Now')
    parser.add_argument('--list', '-l', action='store_true',
                        help='List the contents of the inventory')
    parser.add_argument('--host', action='store',
                        help='List the hosts in the inventory. DEPRECTATED: Ansible now uses _meta returned by --list')
    # prog uses filename since not defined
    parser.add_argument('--version', '-v', action='version',
                        version='%(prog)s 1.0')
    parser.add_argument('--test', '-t', action='store',
                        help='Runs test functions')
    args = parser.parse_args()

    if args.list:
        print(json.dumps(inventory_list()[0], indent=4, default=set_default))
    elif args.host:
        host_inventory = inventory_host(args.host)
        print(json.dumps(host_inventory, indent=4, default=set_default))
    elif args.test:
        # print(appliance_name_validation(args.test))
    else:
        print("Specify either '--host' or '--list'")
        # print(json.dumps(inventory_list()[0], indent=4))  #this is likely the endstate option!


# Entrypoint for the --list option
def inventory_list():
    return [
        # print("List was called")
        snow_api_parser()
    ]


# Puts all the responses together for output
def snow_api_parser():
    # Default 'all' inventory section, contains all hosts and client groups, as well as global vars
    ans_inventory_all = {
        'all': {
            'hosts': snow_api_call_1()['appliance_list'],
            'vars': {},
            'children': snow_api_call_1()['client_groups']
        },
    }

    # Un-grouped inventory items, should be empty/consistent based on API operations
    snow_api_call_ungrouped = {'ungrouped': {
        'hosts': [], 'vars': {}, 'children': [], }}

    # Per-client list and groupings
    snow_api_call_clients = {}  # []

    # Build Client JSON Arrays
    for client in snow_api_call_1()['client_groups']:
        if client in default_group_list:
            continue
        else:
            snow_api_call_clients[client] = {}
            snow_api_call_clients[client]['hosts'] = []
            snow_api_call_clients[client]['vars'] = {}
            snow_api_call_clients[client]['children'] = []

    # Assign Clients to appliances
    for appliance in snow_api_call_1()['appliance_list']:
        if appliance[:4] in snow_api_call_clients.keys():
            client = appliance[:4]
            snow_api_call_clients[client]['hosts'].append(appliance)
        else:
            continue

    # Add all our dictionaries together for the final print to Ansible
    snow_call_result = {}
    snow_call_result.update(snow_api_call_1()['ans_inventory_meta'])
    snow_call_result.update(ans_inventory_all)
    snow_call_result.update(snow_api_call_ungrouped)
    snow_call_result.update(snow_api_call_clients)

    return snow_call_result


# This function performs the actual SNOW API call and creates lists for the inventory parser
def snow_api_call_1():
    query_appliances = {
        'sysparm_fields': 'u_host_name,u_host_vpn_ip_address,u_active'
    }
    response_appliances = requests.get(
        snow_url, headers=snow_headers, params=query_appliances)

    # Loop appliances (and their attributes) returned in .get into lists
    appliance_list = []
    client_groups = default_group_list + []
    ans_inventory_meta = {'_meta': {'hostvars': {}}}

    for appliance in response_appliances.json()['result']:
        if appliance['u_host_name'] == '':
            continue
        else:
            name = appliance['u_host_name']
            client = appliance['u_host_name']
            ip_address = appliance['u_host_vpn_ip_address']
            is_active = appliance['u_active']

        # Append values to appropriate working variable
            appliance_list.append(name)
            client_groups.append(client[:4])
            ans_inventory_meta['_meta']['hostvars'].update({name: {'ansible_host': ip_address, 'ansible_ssh_user': global_ansible_vars()[
                                                           'ans_ssh_user'], 'ansible_ssh_private_key_file': global_ansible_vars()['ans_ssh_priv_key'], 'ansible_ssh_common_args': global_ansible_vars()['ans_ssh_args']}})

    # Return appropriate responses to the parser function
    return {'appliance_list': appliance_list, 'client_groups': remove_dupes(client_groups), 'snow_api_call1_response': response_appliances.json()['result'], 'ans_inventory_meta': ans_inventory_meta}


# Grab JWT token
def ed_token():
    auth_payload = {'email': ed_api_username, 'password': ed_api_password}
    ed_token_response = requests.post(
        'https://secondary-inventory-source.example.com/api/v1/authenticate', data=auth_payload, verify=False)
    return ed_token_response.json()['auth_token']


# Validates appliance is proper generation, if not, return relevant information
def appliance_name_validation(appliance_hostname):
    # redacted to protect the innocent.  Returns a validated IPv4 address.
            )


# For use with secondary source to validate IPs of hosts
def ed_appliance_ip(api_host):
    ed_querystring={'search': api_host}
    ed_headers={'authorization': ed_token()}
    ed_response=requests.get(
        ed_url_appliance, headers=ed_headers, params=ed_querystring, verify=False)
    # print(ed_response.json())
    if ed_response.json()['status'] == 'FAILED':
        return 'Invalid appliance, unable to locate in SNOW or Secondardy Source.'
    else:
        return ed_response.json()['data']['appliance_data'][0]['lo_address']


# Entrypoint for the --host option, This is deprecated with the '_meta' return from --list
def inventory_host(ans_host):
    try:
        ipaddress.ip_address(snow_api_call_host(ans_host))
        return {
            'ansible_ssh_user': global_ansible_vars()['ans_ssh_user'],
            'ansible_ssh_private_key_file': global_ansible_vars()['ans_ssh_priv_key'],
            'ansible_host': snow_api_call_host(ans_host),
            'ansible_ssh_common_args': global_ansible_vars()['ans_ssh_args']
        }
    except:
        return "Host not found in inventory [SNOW or Secondardy Source], validate hostname and/or system status."


# API calls for --host lookups
def snow_api_call_host(api_host):
    snow_querystring = {
        'sysparm_fields': 'u_host_name,u_host_vpn_ip_address,u_asset_name1,owned_by,u_active,u_identification_value,u_host_expanded_key',
        'sysparm_query': 'u_host_name='+api_host
    }
    snow_response = requests.get(
        snow_url, headers=snow_headers, params=snow_querystring)
    try:
        return snow_response.json()['result'][0]['u_host_vpn_ip_address']
    except:
        return ed_appliance_ip(api_host)


# Ansible variables based on SNOW URL
def global_ansible_vars():
    if 'test' in snow_url:
        ans_ssh_user = site_ssh_user_account
        ans_ssh_priv_key = site_ssh_private_key
        ans_ssh_args = site_ssh_args
    elif 'preprod' in snow_url:
        ans_ssh_user = 'is there a preprod'
        ans_ssh_priv_key = 'is there a preprod'
        ans_ssh_args = 'is there a preprod'
    else:
        ans_ssh_user = 'prod_values'
        ans_ssh_priv_key = 'prod_values'
        ans_ssh_args = 'prod_values'

    return {'ans_ssh_user': ans_ssh_user, 'ans_ssh_priv_key': ans_ssh_priv_key, 'ans_ssh_args': ans_ssh_args}


# Starts the party
if __name__ == '__main__':
    read_command_args()
