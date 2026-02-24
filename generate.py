#!/usr/bin/env python3
"""Audio Spec 生成器"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import yaml

from config import CHIP, MODULE_TREE, CLK_INPUTS

FILE = f"audio_spec_{CHIP}"
COLORS = ["#e3f2fd", "#fce4ec", "#e8f5e9", "#fff3e0", "#f3e5f5",
          "#e0f7fa", "#fff8e1", "#fbe9e7", "#e8eaf6", "#f1f8e9"]

# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class Field:
    bits: str
    name: str
    access: str = "R/W"
    default: str = "0"
    desc: str = ""


@dataclass
class Reg:
    name: str
    offset: int
    fields: list[Field] = field(default_factory=list)
    desc: str = ""


@dataclass
class Module:
    name: str
    addr: int = 0
    offset: int = 0
    active: bool = True
    regs: list[Reg] = field(default_factory=list)
    children: list = field(default_factory=list)

# ============================================================================
# 配置查询
# ============================================================================


def get_cfg(*path):
    """从 MODULE_TREE 查找配置值"""
    def search(node, remaining):
        if not remaining:
            return node if not isinstance(node, dict) else node.get("imp")
        key = remaining[0]
        if not isinstance(node, dict):
            return None
        if key in node:
            return search(node[key], remaining[1:])
        for v in node.values():
            if isinstance(v, dict):
                result = search(v, remaining)
                if result is not None:
                    return result
        return None
    return search(MODULE_TREE, list(path))


def check_cond(cond):
    if not cond:
        return True
    path = cond.get("macro_path", [])
    val = get_cfg(*path)
    if val is None:
        return False
    if "eq" in cond:
        return val == cond["eq"]
    if "equals" in cond:
        return val == cond["equals"]
    return bool(val)


def resolve_voice(s: str) -> str:
    sed_imp = get_cfg("sed", "imp")
    vad_imp = get_cfg("vad", "imp")
    algo = "SED" if sed_imp else ("VAD" if vad_imp else None)
    if algo:
        s = s.replace("{VOICE}", algo).replace("{TO_VOICE}", f"TO{algo}")
    return s


def color_for(name: str) -> str:
    base = "_".join(name.split("_")[:-1]) if "_" in name else name
    return COLORS[hash(base) % len(COLORS)]

# ============================================================================
# YAML 解析
# ============================================================================


def parse_fields(raw: list) -> list[Field]:
    if not raw:
        return []
    out = []
    for f in raw:
        active = check_cond(f.get("condition"))
        bits = str(f["bits"])
        out.append(Field(
            bits=bits,
            name=f["name"] if active else "reserved",
            access=f.get("access", "R/W") if active else "R/W",
            default=str(f.get("default", "0")) if active else "0",
            desc=f.get("description", "") if active else "",
        ))
    out.sort(key=lambda x: -int(x.bits.split(":")[0]))
    return out


def expand_regs(raw: list, inst_id: str = None) -> list[Reg]:
    out = []
    for r in raw:
        name = resolve_voice(r["name"])
        if inst_id:
            name = re.sub(r'\{[A-Z_]+\}', inst_id, name)
        fields = parse_fields(r.get("fields", []))
        desc = r.get("description", "")
        offsets = r.get("offsets")

        if isinstance(offsets, dict):
            for idx, off in offsets.items():
                if not check_cond(r.get("conditions", {}).get(idx)):
                    continue
                n = re.sub(r'\{[A-Z_]+\}', idx, name)
                out.append(Reg(n, off, fields, desc))
        else:
            out.append(Reg(name, r.get("offset", 0), fields, desc))
    return out


YAML_CACHE = {}  # {key: (raw_regs, offset, inst_id)}


def load_all_yaml():
    """一次性加载所有 YAML"""
    if YAML_CACHE:
        return
    for p in Path("modules").glob("*.yaml"):
        data = yaml.safe_load(p.read_text())
        mod_name = resolve_voice(data["module"]).lower()
        if not check_cond(data.get("module_condition")):
            continue

        for sec_name, sec_data in data.get("sections", {}).items():
            YAML_CACHE[f"{mod_name}_{sec_name}".lower()] = (
                sec_data.get("registers", []), 0, None)

        for sub_name, sub_data in data.get("submodules", {}).items():
            for iid, icfg in sub_data.get("instances", {}).items():
                if icfg and check_cond(icfg.get("condition")):
                    YAML_CACHE[f"{sub_name}_{iid}".lower()] = (sub_data.get(
                        "registers", []), icfg.get("offset_base", 0), iid)

        for iid, icfg in data.get("instances", {}).items():
            if icfg and check_cond(icfg.get("condition")):
                YAML_CACHE[f"{mod_name}_{iid}".lower()] = (
                    data.get("registers", []), icfg.get("offset_base", 0), iid)

        if not data.get("sections") and not data.get("submodules") and not data.get("instances"):
            YAML_CACHE[mod_name] = (data.get("registers", []), 0, None)


def get_module_info(name: str) -> tuple[list[Reg], int]:
    """获取模块寄存器和偏移"""
    load_all_yaml()
    entry = YAML_CACHE.get(name.lower())
    if entry:
        raw, offset, inst_id = entry
        return expand_regs(raw, inst_id), offset
    return [], 0

# ============================================================================
# 树构建
# ============================================================================


def is_leaf(val) -> bool:
    """判断是否为叶子模块配置（有 imp 字段）"""
    return isinstance(val, dict) and "imp" in val


def is_module_parent(key: str, val: dict) -> bool:
    """是否为模块父节点（小写开头，所有子节点都是叶子）
    如 tdmin, earcrx 等，子节点是实例 A/B/C/CMDC/DMAC 等
    """
    if not key[0].islower():
        return False
    subs = [v for k, v in val.items() if not k.startswith("_")]
    return subs and all(is_leaf(v) for v in subs)


def build_tree() -> list[Module]:
    """根据 MODULE_TREE 构建完整模块树"""
    load_all_yaml()

    def build(key: str, val) -> Module:
        if not isinstance(val, dict):
            return None

        if is_leaf(val):
            addr = val.get("_addr", 0)
            imp = val.get("imp", 0) == 1
            regs, offset = get_module_info(key)
            return Module(key, addr=addr, offset=offset, active=imp, regs=regs)

        if is_module_parent(key, val):
            children = []
            for k, v in val.items():
                if k.startswith("_"):
                    continue
                full_name = f"{key}_{k}"
                addr = v.get("_addr", 0)
                imp = v.get("imp", 0) == 1
                regs, offset = get_module_info(full_name)
                children.append(Module(full_name, addr=addr,
                                offset=offset, active=imp, regs=regs))
            children.sort(key=lambda m: (not m.active, m.offset, m.name))
            return Module(key, children=children)

        addr = val.get("_addr", 0)
        children = [build(k, v)
                    for k, v in val.items() if not k.startswith("_")]
        children = [c for c in children if c]
        if children:
            return Module(key, addr=addr, children=children)
        return None

    return [build(k, v) for k, v in MODULE_TREE.items() if build(k, v)]

# ============================================================================
# HTML 渲染
# ============================================================================


def h(tag, content="", **attrs):
    a = " ".join(f'{k.strip("_")}="{v}"' for k, v in attrs.items())
    return f"<{tag} {a}>{content}</{tag}>" if a else f"<{tag}>{content}</{tag}>"


def table_html(headers, rows):
    thead = h("tr", "".join(h("th", x) for x in headers))
    tbody = "".join(h("tr", "".join(h("td", c) for c in r)) for r in rows)
    return h("table", h("thead", thead) + h("tbody", tbody))


def count_regs(node: Module) -> int:
    """递归统计子树的总寄存器数"""
    if node.children:
        return sum(count_regs(c) for c in node.children)
    return len(node.regs) if node.active else 0


def tree_html(node: Module, base_addr=0, level=0) -> str:
    """渲染地址树，叶子节点包含完整地址信息"""
    color = color_for(node.name)
    base = node.addr if node.addr else base_addr

    if node.children:
        total = count_regs(node)
        cls = " inactive" if total == 0 else ""
        addr = f' <span class="addr">0x{node.addr:08X}</span>' if node.addr else ""
        inner = "".join(tree_html(c, base, level+1) for c in node.children)
        return f'''<details open class="node{cls}">
            <summary style="border-left-color:{color}"><b>{node.name}</b>{addr} <span class="info">({total} regs)</span></summary>
            <div class="children">{inner}</div></details>'''
    else:
        has_regs = node.active and node.regs
        cls = "" if has_regs else " inactive"
        if has_regs:
            mod_base = node.addr if node.addr else base + node.offset * 4
            offsets = [r.offset for r in node.regs]
            start, end = min(offsets), max(offsets)
            abs_start, abs_end = mod_base + start * 4, mod_base + end * 4
            size = (end - start + 1) * 4
            info = f'''<span class="leaf-info">
                <span>Base: 0x{mod_base:08X}</span>
                <span>Range: 0x{abs_start:08X}-0x{abs_end:08X}</span>
                <span>Size: {size}B</span>
                <span>Regs: {len(node.regs)}</span>
            </span>'''
        else:
            info = ''
        return f'<div class="leaf{cls}" style="border-left-color:{color}"><b>{node.name}</b> {info}</div>'


def regs_html(node: Module, base_addr=0) -> str:
    """渲染寄存器树，纯树状结构"""
    color = color_for(node.name)
    cls = "" if node.active else " inactive"
    base = node.addr if node.addr else base_addr

    if node.children:
        inner = "".join(regs_html(c, base) for c in node.children)
        return f'''<details open class="group{cls}">
            <summary style="border-left:3px solid {color}"><b>{node.name}</b></summary>
            <div class="group-content">{inner}</div></details>'''

    if not node.regs:
        if not node.active:
            return f'<div class="module-inactive">{node.name} <span class="inactive-label">- Not in {CHIP}</span></div>'
        return ""

    regs_inner = ""
    mod_base = node.addr if node.addr else base + node.offset * 4  # 模块绝对基地址
    for r in node.regs:
        reg_byte_off = r.offset * 4  # 寄存器在模块内的字节偏移
        abs_addr = mod_base + reg_byte_off
        fields = table_html(["Bits", "Name", "Access", "Default", "Desc"],
                            [[f.bits, f.name, f.access, f.default, f.desc] for f in r.fields]) if r.fields else ""
        addr_info = f'<span class="reg-addr">Base: 0x{mod_base:08X} + Offset: 0x{reg_byte_off:03X} = <b>0x{abs_addr:08X}</b></span>'
        regs_inner += f'''<details><summary><code>{r.name}</code></summary>
            <div class="reg-body">{addr_info}{fields}</div></details>'''

    return f'''<details class="module{cls}" style="--color:{color}">
        <summary><b>{node.name}</b> <span class="info">({len(node.regs)} regs)</span></summary>
        <div class="regs">{regs_inner}</div></details>'''


def clk_html() -> str:
    out = []
    for grp, inputs in CLK_INPUTS.items():
        rows = [[f"I_{grp}_{k}", v] for k, v in inputs.items()]
        out.append(f"<h4>{grp}</h4>" + table_html(["Port", "Driver"], rows))
    return "".join(out)


def macro_html() -> str:
    rows = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.startswith("_"):
                    rows.append([f"{path}.{k}" if path else k, str(v)])
                else:
                    walk(v, f"{path}.{k}" if path else k)
        else:
            rows.append([path, str(node)])
    walk(MODULE_TREE)
    return table_html(["Path", "Value"], rows)


HTML_STYLE = """
:root{--bg:#fff;--fg:#333;--border:#e0e0e0;--hover:#f5f5f5}
body{font-family:system-ui;max-width:1800px;margin:0 auto;padding:20px;color:var(--fg)}
h1,h2{border-bottom:1px solid var(--border);padding-bottom:8px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}
th,td{border:1px solid var(--border);padding:6px 10px;text-align:left;font-family:monospace}
th{background:#f8f8f8;font-family:system-ui}
code{background:#f0f0f0;padding:2px 6px;border-radius:3px;font-size:13px;font-weight:500}
.toolbar{margin:15px 0;display:flex;gap:8px}
.toolbar button{padding:6px 14px;border:1px solid var(--border);background:var(--bg);border-radius:4px;cursor:pointer}
.toolbar button:hover{background:var(--hover)}
.node,.leaf{margin:2px 0}
.node>summary{font-size:14px;padding:5px 8px;cursor:pointer;border-left:3px solid}
.node>summary:hover{background:var(--hover)}
.children{margin-left:16px;padding-left:8px;border-left:1px dashed #ccc}
.leaf{padding:6px 8px;border-left:3px solid;font-size:13px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.leaf-info{display:flex;gap:16px;font-family:monospace;font-size:12px;color:#666}
.leaf-info span{background:#f5f5f5;padding:2px 8px;border-radius:3px}
.inactive{opacity:0.5}
.addr{color:#666;font-family:monospace;font-size:12px}
.info{color:#999;font-size:11px;margin-left:8px}
.group{margin:4px 0}
.group>summary{font-size:14px;padding:5px 8px;cursor:pointer}
.group>summary:hover{background:var(--hover)}
.group-content{margin-left:16px;padding:8px;border-left:1px dashed #ccc}
.module{margin:4px 0;border-left:3px solid var(--color)}
.module>summary{font-size:14px;padding:6px 10px;cursor:pointer;background:var(--color)}
.module.inactive{opacity:0.5}
.module-inactive{padding:4px 10px;font-size:12px;color:#aaa}
.inactive-label{font-size:11px;font-style:italic}
.regs{padding:10px}
.regs details{margin:4px 0;border:1px solid #e0e0e0;border-radius:4px}
.regs summary{padding:6px 10px;font-size:13px;cursor:pointer}
.regs summary:hover{background:#f5f5f5}
.reg-body{padding:12px;background:#fafafa}
.reg-addr{display:block;margin-bottom:10px;font-family:monospace;font-size:13px;color:#555;background:#fff;padding:6px 10px;border-radius:4px;border:1px solid #e0e0e0}
hr{border:none;border-top:1px solid var(--border);margin:25px 0}
"""

HTML_SCRIPT = """
const expandAll=()=>document.querySelectorAll('details').forEach(d=>d.open=true);
const collapseAll=()=>document.querySelectorAll('details').forEach(d=>d.open=false);
"""


def render_html(tree: list[Module]) -> str:
    toolbar = '''<div class="toolbar">
        <button onclick="expandAll()">Expand All</button>
        <button onclick="collapseAll()">Collapse All</button></div>'''

    sections = [
        ("Address Map", '<div class="tree">' + "".join(tree_html(n)
         for n in tree) + '</div>'),
        ("Register Details", '<div class="registers">' +
         "".join(regs_html(n) for n in tree) + '</div>'),
        ("Clock Sources", clk_html()),
        ("Macro Parameters", macro_html()),
    ]

    body = f"<h1>{CHIP} Audio Register Specification</h1>{toolbar}"
    for title, content in sections:
        body += f"<h2>{title}</h2>{content}<hr>"

    return f'''<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{CHIP} Audio Spec</title>
<style>{HTML_STYLE}</style>
<script>{HTML_SCRIPT}</script>
</head><body>{body}</body></html>'''

# ============================================================================
# Markdown 渲染
# ============================================================================


def tree_md(node: Module, base_addr=0, depth=0, prefix="") -> str:
    """渲染树状结构，包含完整地址信息"""
    lines = []
    base = node.addr if node.addr else base_addr

    if node.children:
        total = sum(len(c.regs) for c in node.children)
        addr = f" 0x{node.addr:08X}" if node.addr else ""
        lines.append(f"{prefix}**{node.name}**{addr} ({total} regs)\n")
        for i, c in enumerate(node.children):
            is_child_last = (i == len(node.children) - 1)
            child_prefix = prefix.replace("├─ ", "│  ").replace("└─ ", "   ")
            child_prefix += "└─ " if is_child_last else "├─ "
            lines.append(tree_md(c, base, depth+1, child_prefix))
    else:
        if node.active and node.regs:
            mod_base = node.addr if node.addr else base + node.offset * 4
            offsets = [r.offset for r in node.regs]
            start, end = min(offsets), max(offsets)
            abs_start, abs_end = mod_base + start * 4, mod_base + end * 4
            size = (end - start + 1) * 4
            lines.append(
                f"{prefix}{node.name}  Base:0x{mod_base:08X} Range:0x{abs_start:08X}-0x{abs_end:08X} {size}B {len(node.regs)}regs\n")
        else:
            lines.append(f"{prefix}~~{node.name}~~ (inactive)\n")

    return "".join(lines)


def regs_md(node: Module, base_addr=0, depth=0) -> str:
    level = min(depth + 3, 6)
    base = node.addr if node.addr else base_addr

    if node.children:
        head = f"{'#'*level} {node.name}\n\n"
        return head + "".join(regs_md(c, base, depth+1) for c in node.children)

    if not node.active:
        return f"{'#'*level} ~~{node.name}~~ *Not in {CHIP}*\n\n"
    if not node.regs:
        return f"{'#'*level} {node.name} (0 regs)\n\n"

    lines = [f"{'#'*level} {node.name} ({len(node.regs)} regs)\n\n"]
    mod_base = node.addr if node.addr else base + node.offset * 4
    for r in node.regs:
        reg_byte_off = r.offset * 4
        abs_addr = mod_base + reg_byte_off
        lines.append(f"**{r.name}**\n\n")
        lines.append(
            f"- Base: `0x{mod_base:08X}` + Offset: `0x{reg_byte_off:03X}` = **`0x{abs_addr:08X}`**\n\n")
        if r.fields:
            lines.append(
                "| Bits | Name | Access | Default | Desc |\n|---|---|---|---|---|\n")
            for f in r.fields:
                lines.append(
                    f"| {f.bits} | {f.name} | {f.access} | {f.default} | {f.desc} |\n")
            lines.append("\n")
    return "".join(lines)


def clk_md() -> str:
    lines = ["## Clock Sources\n"]
    for grp, inputs in CLK_INPUTS.items():
        lines.append(f"### {grp}\n| Port | Driver |\n|---|---|\n")
        for k, v in inputs.items():
            lines.append(f"| I_{grp}_{k} | {v} |\n")
    return "".join(lines)


def macro_md() -> str:
    lines = ["## Macro Parameters\n| Path | Value |\n|---|---|\n"]

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.startswith("_"):
                    lines.append(f"| {f'{path}.{k}' if path else k} | {v} |\n")
                else:
                    walk(v, f"{path}.{k}" if path else k)
        else:
            lines.append(f"| {path} | {node} |\n")
    walk(MODULE_TREE)
    return "".join(lines)


def render_md(tree: list[Module]) -> str:
    addr_map = "```\n" + "".join(tree_md(n) for n in tree) + "```\n"
    return "\n".join([
        f"# {CHIP} Audio Register Specification\n",
        "## Address Map\n", addr_map,
        "## Register Details\n", "".join(regs_md(n) for n in tree),
        clk_md(), macro_md(),
    ])

# ============================================================================
# 主流程
# ============================================================================


def main():
    assert Path("modules").exists()

    out = Path("output")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    tree = build_tree()

    md_path = out / f"{FILE}.md"
    md_path.write_text(render_md(tree))
    print(f"输出: {md_path}")

    (out / f"{FILE}.html").write_text(render_html(tree))
    print(f"输出: {out}/{FILE}.html")

    pdf_path = out / f"{FILE}.pdf"
    r = subprocess.run(["pandoc", str(md_path), "-o", str(pdf_path),
                       "--pdf-engine=wkhtmltopdf"], capture_output=True)
    if r.returncode == 0:
        print(f"输出: {pdf_path}")
    else:
        print("PDF 跳过 (需要 pandoc + wkhtmltopdf)")


if __name__ == "__main__":
    main()
