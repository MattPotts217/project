"""Generate Ansible inventory and playbooks from templates."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATES_DIR = _REPO_ROOT / "478_examples" / "templates"
DEFAULT_PLAYBOOK_DIR = _REPO_ROOT / "478_examples" / "playbook"

DEFAULT_SSH_CONFIG = "/home/fabric/work/fabric_config/ssh_config"
DEFAULT_RKE2_TOKEN = "fabric-rke2-cluster-token"
DEFAULT_DATAPLANE_GATEWAY = "192.168.1.254"
DEFAULT_PLAYBOOKS = ("playbook-prereqs.yml", "playbook-rke2.yml")


def fill_template(name, templates_dir=None, **replacements):
    """Replace ``__KEY__`` placeholders in a template file and return the text."""
    templates_dir = Path(templates_dir or DEFAULT_TEMPLATES_DIR)
    text = (templates_dir / name).read_text()
    for key, value in replacements.items():
        text = text.replace(f"__{key}__", str(value))
    return text


def _host_yaml(nd, templates_dir):
    node = nd["node"]
    return fill_template(
        "host.yml.template",
        templates_dir=templates_dir,
        HOST_NAME=nd["name"],
        ANSIBLE_HOST=node.get_management_ip(),
        ANSIBLE_USER=node.get_username(),
        PYTHON=nd["python"],
        PRIVATE_IP=nd["private_ip"],
    )


def generate_rke2_playbooks(
    slice,
    fablib,
    network_name,
    templates_dir=None,
    playbook_dir=None,
    ssh_config=DEFAULT_SSH_CONFIG,
    rke2_token=DEFAULT_RKE2_TOKEN,
    dataplane_gateway=DEFAULT_DATAPLANE_GATEWAY,
    server_node_name="node1",
    playbooks=DEFAULT_PLAYBOOKS,
):
    """Fill Ansible templates from a live FABRIC slice and write playbook files.

    ``node1`` (or ``server_node_name``) is placed in ``rke2_servers``; remaining
    nodes go in ``rke2_agents``. Returns the generated inventory.yml text.
    """
    templates_dir = Path(templates_dir or DEFAULT_TEMPLATES_DIR)
    playbook_dir = Path(playbook_dir or DEFAULT_PLAYBOOK_DIR)
    playbook_dir.mkdir(parents=True, exist_ok=True)

    node_defs = []
    for node in slice.get_nodes():
        name = node.get_name()
        private_ip = str(node.get_interface(network_name=network_name).get_ip_addr())
        group = "rke2_servers" if name == server_node_name else "rke2_agents"
        stdout, _stderr = node.execute(
            "python3 -c 'import sys; print(sys.executable)'",
            quiet=True,
        )
        node_defs.append(
            {
                "name": name,
                "private_ip": private_ip,
                "group": group,
                "node": node,
                "python": stdout.strip(),
            }
        )

    slice_key = fablib.get_default_slice_key()["slice_private_key_file"]
    server_ip = next(nd["private_ip"] for nd in node_defs if nd["group"] == "rke2_servers")
    values = dict(
        ANSIBLE_SSH_PRIVATE_KEY_FILE=slice_key,
        SSH_CONFIG=ssh_config,
        RKE2_TOKEN=rke2_token,
        RKE2_SERVER_PRIVATE_IP=server_ip,
        DATAPLANE_GATEWAY=dataplane_gateway,
        RKE2_SERVERS_HOSTS="".join(
            _host_yaml(nd, templates_dir) for nd in node_defs if nd["group"] == "rke2_servers"
        ).rstrip("\n"),
        RKE2_AGENTS_HOSTS="".join(
            _host_yaml(nd, templates_dir) for nd in node_defs if nd["group"] == "rke2_agents"
        ).rstrip("\n"),
    )

    inventory = fill_template("inventory.yml.template", templates_dir=templates_dir, **values)
    (playbook_dir / "inventory.yml").write_text(inventory)
    for playbook in playbooks:
        (playbook_dir / playbook).write_text(
            fill_template(f"{playbook}.template", templates_dir=templates_dir, **values)
        )

    print(f"Wrote Ansible files from {templates_dir} -> {playbook_dir}")
    return inventory
