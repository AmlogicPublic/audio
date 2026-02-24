#!/usr/bin/env python3
"""
Audio Spec 生成器 - 生成 HTML/MD/PDF
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import yaml

from config import CHIP, MACROS, MODULES, CLK_INPUTS

FILE = f"audio_spec_{CHIP}"

# ============================================================================
# 数据类型定义
# ============================================================================


@dataclass
class FieldInfo:
    bits: str
    name: str
    access: str
    default: str
    description: str


@dataclass
class RegEntry:
    name: str
    offset: int
    fields: list[FieldInfo] = field(default_factory=list)
    description: str = ""


@dataclass
class ModuleSection:
    title: str
    active: bool
    module_type: str  # 用于颜色分组
    parent: str = ""  # 父模块名
    registers: list[RegEntry] = field(default_factory=list)


@dataclass
class TreeNode:
    name: str
    addr: int = 0           # 基地址（0 表示逻辑分组或用偏移）
    offset: int = 0         # 相对父级偏移
    reg_start: int = 0
    reg_end: int = 0
    reg_count: int = 0
    active: bool = True
    children: list = field(default_factory=list)


# ============================================================================
# 颜色映射
# ============================================================================

MODULE_COLORS = [
    "#e3f2fd", "#fce4ec", "#e8f5e9", "#fff3e0", "#f3e5f5",
    "#e0f7fa", "#fff8e1", "#fbe9e7", "#e8eaf6", "#f1f8e9",
    "#e0f2f1", "#ede7f6", "#ffebee", "#e1f5fe", "#f9fbe7",
]


def get_module_color(module_type: str) -> str:
    h = hash(module_type) % len(MODULE_COLORS)
    return MODULE_COLORS[h]


# ============================================================================
# 宏处理
# ============================================================================

MODULE_GATE = {
    "pdm_a": ("pdm", "A", "imp"), "pdm_b": ("pdm", "B", "imp"),
    "resample_a": ("resample", "A", "imp"), "resample_b": ("resample", "B", "imp"),
    "resample_c": ("resample", "C", "imp"), "eq_drc": ("eqdrc", "imp"),
    "earctx_cmdc": ("earctx", "imp"), "earctx_dmac": ("earctx", "imp"),
    "earctx_top": ("earctx", "imp"), "earcrx_cmdc": ("earcrx", "imp"),
    "earcrx_dmac": ("earcrx", "imp"), "earcrx_top": ("earcrx", "imp"),
    "locker_a": ("locker", "A", "imp"), "locker_b": ("locker", "B", "imp"),
    "acc_wrapper_asrc": ("acc_wrapper", "ASRC", "imp"),
    "acc_wrapper_eqdrc": ("acc_wrapper", "EQDRC", "imp"),
    "sed": ("voice", "algo"), "vad": ("voice", "algo"),
}


def get_macro(*path):
    node = MACROS
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return 0
        node = node[p]
    return node if not isinstance(node, dict) else node.get("imp", 0)


def check_condition(cond):
    if not cond:
        return True
    path = cond.get("macro_path", [])
    node = MACROS
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return False
        node = node[p]
    if "eq" in cond:
        return node == cond["eq"]
    if "ne" in cond:
        return node != cond["ne"]
    return bool(node)


def is_active(mod_name):
    gate = MODULE_GATE.get(mod_name)
    if gate is None:
        return False
    if mod_name in ("sed", "vad"):
        return get_macro("voice", "imp") and get_macro("voice", "algo") == mod_name
    return get_macro(*gate)


def resolve_voice(name: str) -> str:
    voice_algo = get_macro("voice", "algo")
    if isinstance(voice_algo, str):
        algo_upper = voice_algo.upper()
        name = name.replace("{VOICE}", algo_upper).replace(
            "{TO_VOICE}", f"TO{algo_upper}")
    return name


# ============================================================================
# 数据解析
# ============================================================================

def parse_bits(bits_str) -> tuple[int, int]:
    bits_str = str(bits_str)
    if ":" in bits_str:
        parts = bits_str.split(":")
        return int(parts[0]), int(parts[1])
    return int(bits_str), int(bits_str)


def parse_fields(fields: list) -> list[FieldInfo]:
    if not fields:
        return []
    parsed = []
    for f in fields:
        high, low = parse_bits(f["bits"])
        active = check_condition(f.get("condition"))
        bits_str = f"{high}:{low}" if high != low else str(high)
        parsed.append(FieldInfo(
            bits=bits_str,
            name=f["name"] if active else "reserved",
            access=f.get("access", "R/W") if active else "R/W",
            default=f.get("default", "0") if active else "0",
            description=f.get("description", "") if active else "reserved",
        ))
    parsed.sort(key=lambda x: -int(x.bits.split(":")[0]))
    return parsed


def apply_rename_rules(name, rules):
    if not rules:
        return name
    for rule in rules:
        name = re.sub(rule.get("pattern", ""),
                      rule.get("replacement", ""), name)
    return name


def expand_register(reg, rename_rules=None, inst_id=None) -> list[RegEntry]:
    rname = reg["name"]
    if rename_rules:
        rname = apply_rename_rules(rname, rename_rules)
    if inst_id:
        rname = re.sub(r'\{[A-Z_]+\}', inst_id, rname)

    fields = parse_fields(reg.get("fields", []))
    desc = reg.get("description", "")
    offsets = reg.get("offsets")
    entries = []

    if reg.get("voice_param"):
        rname = resolve_voice(rname)
        entries.append(RegEntry(rname, reg.get("offset", 0), fields, desc))
    elif isinstance(offsets, dict):
        for idx in reg.get("instances", list(offsets.keys())):
            if idx not in offsets:
                continue
            if not check_condition(reg.get("conditions", {}).get(idx)):
                continue
            n = re.sub(r'\{[A-Z_]+\}', idx, rname)
            entries.append(RegEntry(n, offsets[idx], fields, desc))
    else:
        entries.append(RegEntry(rname, reg.get("offset", 0), fields, desc))

    return entries


def get_module_type(name: str) -> str:
    parts = name.split("_")
    if len(parts) >= 2 and parts[-1].isalpha() and len(parts[-1]) <= 2:
        return "_".join(parts[:-1])
    return name


def parse_module(yaml_path: Path) -> list[ModuleSection]:
    data = yaml.safe_load(yaml_path.read_text())
    module_name = data["module"]
    module_active = check_condition(
        data.get("module_condition")) and check_condition(data.get("arch_condition"))
    sections_data = data.get("sections", {})
    instances = data.get("instances", {})
    registers = data.get("registers", [])
    parent_base = data.get("parent_base", "")

    result = []

    if sections_data:
        for sec_name, sec_data in sections_data.items():
            title = resolve_voice(f"{module_name}_{sec_name}")
            key = title.lower()
            active = module_active and (
                key not in MODULE_GATE or is_active(key))
            regs = [e for r in sec_data.get("registers", [])
                    for e in expand_register(r)] if active else []
            result.append(ModuleSection(
                title, active, get_module_type(title), parent_base, regs))
    elif instances:
        for inst_id, inst_cfg in instances.items():
            title = resolve_voice(f"{module_name}_{inst_id}")
            key = title.lower()
            cond = inst_cfg.get("condition") if inst_cfg else None
            active = module_active and check_condition(cond) and (
                key not in MODULE_GATE or is_active(key))
            rename_rules = inst_cfg.get("rename_rules") if inst_cfg else None
            regs = [e for r in registers for e in expand_register(
                r, rename_rules, inst_id)] if active else []
            result.append(ModuleSection(
                title, active, get_module_type(title), parent_base, regs))
    else:
        title = resolve_voice(module_name)
        key = title.lower()
        active = module_active and (key not in MODULE_GATE or is_active(key))
        regs = [e for r in registers for e in expand_register(r)] if active else [
        ]
        result.append(ModuleSection(
            title, active, get_module_type(title), parent_base, regs))

    return result


def calc_reg_range(registers: list) -> tuple[int, int]:
    if not registers:
        return 0, 0
    offsets = []
    for reg in registers:
        if "offsets" in reg and isinstance(reg["offsets"], dict):
            offsets.extend(reg["offsets"].values())
        else:
            offsets.append(reg.get("offset", 0))
    return min(offsets), max(offsets)


def build_address_map() -> list[TreeNode]:
    """构建完整的模块地址树"""
    modules_dir = Path("modules")
    parent_children = {}  # parent_base -> [TreeNode]
    section_modules = {}  # module_name -> (active, [TreeNode])
    standalone = []       # [TreeNode]

    for yaml_path in sorted(modules_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text())
        module_active = check_condition(data.get("module_condition"))
        module_name = data["module"]
        parent_base = data.get("parent_base")
        instances = data.get("instances", {})
        sections = data.get("sections", {})
        registers = data.get("registers", [])

        if sections:
            sec_list = []
            for sec_name, sec_data in sections.items():
                key = f"{module_name.lower()}_{sec_name.lower()}"
                sec_regs = sec_data.get("registers", [])
                reg_min, reg_max = calc_reg_range(sec_regs)
                addr = MODULES.get(key, 0)
                active = module_active and (key not in MODULE_GATE or is_active(key))
                sec_list.append(TreeNode(
                    name=f"{module_name}_{sec_name}", addr=addr,
                    reg_start=reg_min, reg_end=reg_max, reg_count=len(sec_regs), active=active,
                ))
            section_modules[module_name] = (module_active, sec_list)

        elif instances:
            for inst_id, inst_cfg in instances.items():
                if not inst_cfg:
                    continue
                cond = inst_cfg.get("condition")
                active = module_active and check_condition(cond)
                offset_base = inst_cfg.get("offset_base", 0)
                reg_min, reg_max = calc_reg_range(registers)
                child_name = f"{module_name}_{inst_id}"
                key = child_name.lower()
                node = TreeNode(
                    name=child_name, addr=MODULES.get(key, 0), offset=offset_base,
                    reg_start=reg_min, reg_end=reg_max, reg_count=len(registers),
                    active=active and (key not in MODULE_GATE or is_active(key)),
                )
                if parent_base:
                    parent_children.setdefault(parent_base, []).append(node)
                elif key in MODULES:
                    standalone.append(node)
        else:
            key = module_name.lower()
            if key in MODULES:
                reg_min, reg_max = calc_reg_range(registers)
                active = module_active and (key not in MODULE_GATE or is_active(key))
                standalone.append(TreeNode(
                    name=module_name, addr=MODULES[key],
                    reg_start=reg_min, reg_end=reg_max, reg_count=len(registers), active=active,
                ))

    tree = []
    for parent_key in sorted(parent_children.keys()):
        children = sorted(parent_children[parent_key], key=lambda x: x.offset)
        tree.append(TreeNode(name=parent_key, addr=MODULES.get(parent_key, 0), children=children))

    for mod_name in sorted(section_modules.keys()):
        mod_active, sec_list = section_modules[mod_name]
        tree.append(TreeNode(name=mod_name, active=mod_active, children=sorted(sec_list, key=lambda x: x.addr)))

    if standalone:
        tree.append(TreeNode(name="Other", children=sorted(standalone, key=lambda x: x.addr)))

    return tree


# ============================================================================
# HTML 渲染
# ============================================================================

def html_table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    cls_attr = f' class="{cls}"' if cls else ""
    thead = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    tbody = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table{cls_attr}><thead>{thead}</thead><tbody>{tbody}</tbody></table>"


def render_fields_html(fields: list[FieldInfo]) -> str:
    if not fields:
        return ""
    headers = ["Bits", "Name", "Access", "Default", "Description"]
    rows = [[f.bits, f.name, f.access, f.default, f.description]
            for f in fields]
    return html_table(headers, rows, "fields")


def render_tree_node_html(node: TreeNode, level: int = 0) -> str:
    """递归渲染树节点"""
    html = []
    color = get_module_color(get_module_type(node.name))
    cls = "" if node.active else " inactive"

    if node.children:
        total_regs = sum(c.reg_count for c in node.children)
        addr_str = f' <span class="addr">0x{node.addr:08X}</span>' if node.addr else ''
        html.append(f'<details open class="tree-node level-{level}{cls}">')
        html.append(f'<summary style="border-left-color:{color}">')
        html.append(f'<b>{node.name}</b>{addr_str} <span class="info">({total_regs} regs)</span>')
        html.append('</summary>')
        html.append('<div class="tree-children">')
        for child in node.children:
            html.append(render_tree_node_html(child, level + 1))
        html.append('</div></details>')
    else:
        abs_start = node.offset + node.reg_start
        abs_end = node.offset + node.reg_end
        size = (abs_end - abs_start + 1) * 4
        addr_str = f'0x{node.addr:08X}' if node.addr else f'@0x{node.offset:03X}'
        html.append(f'<div class="tree-leaf{cls}" style="border-left-color:{color}">')
        html.append(f'<span class="name">{node.name}</span> ')
        html.append(f'<span class="addr">{addr_str}</span> ')
        if node.active:
            html.append(f'<span class="range">0x{abs_start:03X}-0x{abs_end:03X}</span> ')
            html.append(f'<span class="info">{size}B, {node.reg_count} regs</span>')
        html.append('</div>')

    return "".join(html)


def render_address_map_html() -> str:
    tree = build_address_map()
    html = ['<div class="tree" id="address-map">']
    for node in tree:
        html.append(render_tree_node_html(node))
    html.append('</div>')
    return "".join(html)


def render_module_card_html(sec: ModuleSection) -> str:
    """渲染单个模块的寄存器卡片"""
    color = get_module_color(sec.module_type)
    cls = "card inactive" if not sec.active else "card"
    html = [f'<details class="{cls}" style="--card-color:{color}">']
    html.append(f'<summary><b>{sec.title}</b>')
    if sec.active:
        html.append(f' <span class="info">({len(sec.registers)} regs)</span>')
    else:
        html.append(f' <span class="inactive-label">Not in {CHIP}</span>')
    html.append('</summary>')

    if sec.active:
        html.append('<div class="reg-list">')
        for reg in sec.registers:
            html.append(f'<details><summary><code>{reg.name}</code> ')
            html.append(f'<span class="addr">0x{reg.offset:03X}</span></summary>')
            html.append('<div class="reg-content">')
            if reg.description:
                html.append(f'<p class="desc">{reg.description}</p>')
            html.append(render_fields_html(reg.fields))
            html.append('</div></details>')
        html.append('</div>')
    html.append('</details>')
    return "".join(html)


def render_registers_html(all_sections: list[ModuleSection], tree: list[TreeNode]) -> str:
    """按树结构渲染寄存器详情"""
    sec_map = {sec.title.lower(): sec for sec in all_sections}

    def render_node(node: TreeNode) -> str:
        html = []
        color = get_module_color(get_module_type(node.name))
        cls = "" if node.active else " inactive"

        if node.children:
            html.append(f'<details open class="reg-group{cls}">')
            html.append(f'<summary style="border-left: 3px solid {color}"><b>{node.name}</b></summary>')
            html.append('<div class="reg-group-content">')
            for child in node.children:
                html.append(render_node(child))
            html.append('</div></details>')
        else:
            sec = sec_map.get(node.name.lower())
            if sec:
                html.append(render_module_card_html(sec))
        return "".join(html)

    html = ['<div class="registers">']
    for node in tree:
        html.append(render_node(node))
    html.append('</div>')
    return "".join(html)


def render_clk_table_html() -> str:
    parts = []
    for group, inputs in CLK_INPUTS.items():
        rows = [[f"I_{group}_{suffix}", driver]
                for suffix, driver in inputs.items()]
        parts.append(f"<h4>{group}</h4>" +
                     html_table(["Port", "Driver"], rows))
    return "".join(parts)


def render_macro_table_html() -> str:
    rows = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        else:
            rows.append([path, str(node)])
    walk(MACROS)
    return html_table(["Path", "Value"], rows)


HTML_STYLE = """
:root { --bg: #fff; --fg: #333; --border: #e0e0e0; --hover: #f5f5f5; }
body { font-family: system-ui, -apple-system, sans-serif; max-width: 1400px; margin: 0 auto; padding: 20px; color: var(--fg); }
h1, h2 { border-bottom: 1px solid var(--border); padding-bottom: 8px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
th, td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
th { background: #f8f8f8; }
code { background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }

.toolbar { margin: 15px 0; display: flex; gap: 8px; flex-wrap: wrap; }
.toolbar button { padding: 6px 14px; border: 1px solid var(--border); background: var(--bg); border-radius: 4px; cursor: pointer; }
.toolbar button:hover { background: var(--hover); }

.tree { margin: 10px 0; }
.tree-node { margin: 2px 0; }
.tree-node > summary { font-size: 14px; padding: 5px 8px; cursor: pointer; border-left: 3px solid; margin-left: -3px; }
.tree-node > summary:hover { background: var(--hover); }
.tree-children { margin-left: 16px; padding-left: 8px; border-left: 1px dashed #ccc; }
.tree-leaf { padding: 4px 8px; border-left: 3px solid; margin: 2px 0; font-size: 13px; }
.tree-node.inactive > summary, .tree-leaf.inactive { opacity: 0.5; }
.tree .addr { color: #666; font-family: monospace; font-size: 12px; }
.tree .range { color: #888; font-family: monospace; font-size: 11px; margin-left: 4px; }
.tree .info { color: #999; font-size: 11px; margin-left: 8px; }
.tree .name { font-weight: 500; }

.registers { margin: 10px 0; }
.reg-group { margin: 4px 0; }
.reg-group > summary { font-size: 14px; padding: 5px 8px; cursor: pointer; margin-left: -3px; }
.reg-group > summary:hover { background: var(--hover); }
.reg-group.inactive > summary { opacity: 0.5; }
.reg-group-content { margin-left: 16px; padding: 8px; border-left: 1px dashed #ccc; display: flex; flex-wrap: wrap; gap: 8px; }
.module-cards { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 0; }
.card { border: 1px solid var(--border); border-radius: 6px; border-top: 3px solid var(--card-color); min-width: 200px; flex: 1; max-width: 350px; }
.card > summary { padding: 8px 12px; cursor: pointer; background: var(--card-color); border-radius: 3px 3px 0 0; }
.card.inactive { opacity: 0.5; background: #f5f5f5; }
.card.inactive > summary { background: #eee; }
.inactive-label { font-size: 11px; color: #999; font-style: italic; }
.reg-list { padding: 8px; max-height: 400px; overflow-y: auto; }
.reg-list details { margin: 2px 0; border: 1px solid #eee; border-radius: 4px; }
.reg-list summary { padding: 4px 8px; font-size: 12px; cursor: pointer; }
.reg-list summary:hover { background: #fafafa; }
.reg-content { padding: 8px; background: #fafafa; }
.desc { margin: 0 0 8px 0; font-size: 12px; color: #666; }
.fields { font-size: 11px; }

hr { border: none; border-top: 1px solid var(--border); margin: 25px 0; }
"""

HTML_SCRIPT = """
const expandAll = () => document.querySelectorAll('details').forEach(d => d.open = true);
const collapseAll = () => document.querySelectorAll('details').forEach(d => d.open = false);
const toggleModules = () => document.querySelectorAll('.card, .tree > details').forEach(d => d.open = !d.open);
const toggleRegs = () => document.querySelectorAll('.reg-list details').forEach(d => d.open = !d.open);
"""


def render_page_html(title: str, sections: list[tuple[str, str]]) -> str:
    toolbar = """<div class="toolbar">
        <button onclick="expandAll()">Expand All</button>
        <button onclick="collapseAll()">Collapse All</button>
        <button onclick="toggleModules()">Toggle Modules</button>
        <button onclick="toggleRegs()">Toggle Registers</button>
    </div>"""

    body = f"<h1>{title}</h1>{toolbar}"
    for sec_title, content in sections:
        if sec_title:
            body += f"<h2>{sec_title}</h2>"
        body += content + "<hr>"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{HTML_STYLE}</style>
<script>{HTML_SCRIPT}</script>
</head><body>{body}</body></html>"""


# ============================================================================
# Markdown 渲染
# ============================================================================

def render_fields_md(fields: list[FieldInfo]) -> str:
    if not fields:
        return ""
    lines = ["| Bits | Name | Access | Default | Description |",
             "|------|------|--------|---------|-------------|"]
    for f in fields:
        lines.append(
            f"| {f.bits} | {f.name} | {f.access} | {f.default} | {f.description} |")
    return "\n".join(lines)


def render_address_map_md() -> str:
    tree = build_address_map()
    lines = ["## Address Map", ""]

    def render_node_md(node: TreeNode, depth: int = 0) -> None:
        indent = "  " * depth
        if node.children:
            total_regs = sum(c.reg_count for c in node.children)
            addr_str = f" @ 0x{node.addr:08X}" if node.addr else ""
            lines.append(f"{'#' * (depth + 3)} {node.name}{addr_str} ({total_regs} regs)")
            lines.append("")
            for child in node.children:
                render_node_md(child, depth + 1)
        else:
            abs_start = node.offset + node.reg_start
            abs_end = node.offset + node.reg_end
            size = (abs_end - abs_start + 1) * 4
            addr_str = f"0x{node.addr:08X}" if node.addr else f"@0x{node.offset:03X}"
            if not node.active:
                lines.append(f"{indent}- ~~{node.name}~~ {addr_str}")
            else:
                lines.append(f"{indent}- **{node.name}** {addr_str} (0x{abs_start:03X}-0x{abs_end:03X}, {size}B, {node.reg_count} regs)")

    for node in tree:
        render_node_md(node)
        lines.append("")

    return "\n".join(lines)


def render_registers_md(all_sections: list[ModuleSection]) -> str:
    lines = ["## Register Details", ""]

    grouped = {}
    for sec in all_sections:
        parent = sec.parent or "standalone"
        grouped.setdefault(parent, []).append(sec)

    for parent in sorted(grouped.keys()):
        sections = grouped[parent]
        parent_name = parent if parent != "standalone" else "Standalone"
        lines.append(f"### {parent_name}")
        lines.append("")

        for sec in sections:
            if not sec.active:
                lines.append(f"#### ~~{sec.title}~~ - *Not in {CHIP}*")
                lines.append("")
                continue

            lines.append(f"#### {sec.title} ({len(sec.registers)} regs)")
            lines.append("")
            for reg in sec.registers:
                lines.append(f"##### {reg.name} @ 0x{reg.offset:03X}")
                lines.append("")
                if reg.description:
                    lines.append(reg.description)
                    lines.append("")
                lines.append(render_fields_md(reg.fields))
                lines.append("")

    return "\n".join(lines)


def render_clk_md() -> str:
    lines = ["## Clock Sources", ""]
    for group, inputs in CLK_INPUTS.items():
        lines.append(f"### {group}")
        lines.append("| Port | Driver |")
        lines.append("|------|--------|")
        for suffix, driver in inputs.items():
            lines.append(f"| I_{group}_{suffix} | {driver} |")
        lines.append("")
    return "\n".join(lines)


def render_macro_md() -> str:
    lines = ["## Macro Parameters", "", "| Path | Value |", "|------|-------|"]

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        else:
            lines.append(f"| {path} | {node} |")
    walk(MACROS)
    return "\n".join(lines)


# ============================================================================
# 主流程
# ============================================================================

def main():
    modules_dir = Path("modules")
    assert modules_dir.exists()

    output_dir = Path("output")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    all_sections = [
        sec for yaml_path in sorted(modules_dir.glob("*.yaml"))
        for sec in parse_module(yaml_path)
    ]

    # 构建地址树
    tree = build_address_map()

    # HTML
    html_sections = [
        ("Address Map", render_address_map_html()),
        ("Register Details", render_registers_html(all_sections, tree)),
        ("Clock Sources", render_clk_table_html()),
        ("Macro Parameters", render_macro_table_html()),
    ]
    html = render_page_html(
        f"{CHIP} Audio Register Specification", html_sections)
    html_path = output_dir / f"{FILE}.html"
    html_path.write_text(html)
    print(f"输出: {html_path}")

    # Markdown
    md_content = "\n".join([
        f"# {CHIP} Audio Register Specification",
        "",
        render_address_map_md(),
        render_registers_md(all_sections),
        render_clk_md(),
        render_macro_md(),
    ])
    md_path = output_dir / f"{FILE}.md"
    md_path.write_text(md_content)
    print(f"输出: {md_path}")

    # PDF (via pandoc)
    pdf_path = output_dir / f"{FILE}.pdf"
    result = subprocess.run(
        ["pandoc", str(md_path), "-o", str(pdf_path),
         "--pdf-engine=wkhtmltopdf"],
        capture_output=True
    )
    if result.returncode == 0:
        print(f"输出: {pdf_path}")
    else:
        print("PDF 跳过 (需要: sudo apt install pandoc wkhtmltopdf)")


if __name__ == "__main__":
    main()
