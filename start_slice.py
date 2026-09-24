import random
import time
import os
import subprocess
import sys

from ipaddress import IPv4Network
from pathlib import Path

from fabrictestbed_extensions.fablib.fablib import FablibManager as fablib_manager

os.environ['FABRIC_RC_LOCATION'] = str(Path.cwd() / 'keys' / 'fabric_rc')

# Find repo root
for _candidate in [Path.cwd(), *Path.cwd().parents]:
    if (_candidate / "utils" / "ansible.py").exists():
        sys.path.insert(0, str(_candidate))
        break
else:
    raise ImportError("Could not find repo root containing utils/ansible.py")

from utils.ansible import generate_rke2_playbooks

# Set up fabric
os.chdir(Path.cwd())
fabric_rc_path = str(Path.cwd() / 'keys' / 'fabric_rc')

fablib = fablib_manager(fabric_rc=fabric_rc_path)
fablib.verify_and_configure()

# Find a Site With Enough Capacity

resources = fablib.get_resources()
resources.update()
siteList = resources.get_site_names()

nodesReq,coresReq,ramReq = 2,8,10

# Scale up quite a bit to ensure there are plenty of resources available.
totalCoreAvail = nodesReq * coresReq * 2
totalRamAvail = nodesReq * ramReq * 2

for siteName in siteList:
    cores = resources.get_core_available(siteName)
    ram = resources.get_ram_available(siteName)
    if cores < totalCoreAvail or ram < totalRamAvail:
        print(f"{siteName} does not have enough free cores/RAM")
        continue
    sliceName = "project-" + siteName
    network_name = "project"

    # Clean up left-over slices with the same names
    existing = None
    for s in fablib.get_slices():
        if s.get_name() == sliceName:
            print(f"Deleting existing slice: {sliceName}")
            s.delete()
            break

    # Set up new slice
    slice = fablib.new_slice(name=sliceName)
    net = slice.add_l2network(name=network_name, subnet=IPv4Network("192.168.1.0/24"))
    
    for i in range(1, nodesReq + 1):
        node = slice.add_node(name=f"node{i}",
                              site=siteName,
                              cores=coresReq,ram=ramReq,disk=50,
                              image="default_ubuntu_22",)
        iface = node.add_component(model="NIC_Basic", name="nic").get_interfaces()[0]
        iface.set_mode("config")
        net.add_interface(iface)

    print("==========================================================================")
    try:
        print(f"Submitting slice at {siteName}...")
        slice.submit(progress=False)
    except Exception as e:
        print(f"{siteName}: slice submission failed with error {e}")
        try:
            slice.delete()
        except Exception:
            pass
        continue

    while True:
        time.sleep(10)
        slice.update()
        slice_state = slice.get_state()
        print(f"Slice state: {slice_state}")
        if slice_state == "Closing":
            print(f"Need to find new site")
            break
        else: 
            print("Slice stable:", slice.isStable())
        nodes = slice.get_nodes()
        if all(node.get_management_ip() is not None for node in nodes):
            for node in nodes:
                print("----", node.get_name(), "----")
                print("management ip:", node.get_management_ip())
                print(node.get_ssh_command())
            break
            
    # Setup networking
    for i in range(nodesReq):
        node = slice.get_node(name=f"node{i + 1}")
        iface = node.get_interface(network_name=network_name)
        iface.ip_link_up()
        iface.ip_addr_add(
            addr=f"192.168.1.{i + 1}",
            subnet=IPv4Network("192.168.1.0/24"),
        )
        print(f"{node.get_name()} -> 192.168.1.{i + 1}")
    
    # Ansible preparation: generate inventory + playbooks from templates
    generate_rke2_playbooks(slice, fablib, network_name)

    # Playbook: Host Prerequisites

    Path("logs").mkdir(exist_ok=True)
    log_path = Path("logs") / f"{siteName}-ansible.log"

    with open(log_path, "w") as log_file:
        print("==== Running prerequisite playbook ====")
        prereq_result = subprocess.run(["ansible-playbook","-i","playbook/inventory.yml","playbook/playbook-prereqs.yml",],
            stdout=log_file,stderr=subprocess.STDOUT,text=True,
        )
    
        if prereq_result.returncode != 0:
            print(f"{siteName}: prerequisite playbook failed")
            slice.delete()
            continue
    
        print("==== Running RKE2 playbook ====")
        rke2_result = subprocess.run(["ansible-playbook","-i","playbook/inventory.yml","playbook/playbook-rke2.yml",],
            stdout=log_file,stderr=subprocess.STDOUT,text=True,
        )
    
        if rke2_result.returncode != 0:
            print(f"{siteName}: RKE2 playbook failed")
            slice.delete()
            continue

    # RKE2 Validation

    server = slice.get_node("node1")
    print("==== nodes ====")
    stdout, stderr = server.execute("kubectl get nodes -o wide", quiet=True)
    print(stdout)
    
    print("==== nginx-demo ====")
    stdout, stderr = server.execute("kubectl get pods -l app=nginx-demo -o wide", quiet=True)
    print(stdout)

    print("==== nginx-demo ====")
    stdout, stderr = server.execute("kubectl get svc -l app=nginx-demo -o wide", quiet=True)
    print(stdout)
    
    print("==== curl via NodePort on dataplane ====")
    stdout, stderr = server.execute("curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.1:30080/", quiet=True)
    if stdout.strip() == "200":
        print(f"{siteName} works ...")
        break
