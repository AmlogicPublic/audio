#!/usr/bin/env python3
"""
Audio Spec 生成器
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
class RegEntry:
    """展开后的单个寄存器条目"""
    name: str
    offset: int
    fields: list = field(default_factory=list)
    description: str = ""


@dataclass
class ModuleSection:
    """模块的一个段落（实例）"""
    title: str           # 如 "RESAMPLE_A", "EARCTX_CMDC"
    gate_key: str        # MODULE_GATE 查找 key
    registers: list      # 原始寄存器定义列表
    rename_rules: list = None
    has_parent: bool = False  # 是否是父模块的子寄存器
    condition: dict = None    # 实例级条件
    inst_id: str = None       # 实例 ID（用于替换 {X} 等占位符）

# ============================================================================
# 辅助定义（派生规则，放这里不污染 config）
# ============================================================================


# 模块 gate 映射：模块名 → MACROS 路径
MODULE_GATE = {
    "pdm_a":         ("pdm", "A", "imp"),
    "pdm_b":         ("pdm", "B", "imp"),
    "resample_a":    ("resample", "A", "imp"),
    "resample_b":    ("resample", "B", "imp"),
    "resample_c":    ("resample", "C", "imp"),
    "eq_drc":        ("eqdrc", "imp"),
    "earctx_cmdc":   ("earctx", "imp"),
    "earctx_dmac":   ("earctx", "imp"),
    "earctx_top":    ("earctx", "imp"),
    "earcrx_cmdc":   ("earcrx", "imp"),
    "earcrx_dmac":   ("earcrx", "imp"),
    "earcrx_top":    ("earcrx", "imp"),
    "locker_a":      ("locker", "A", "imp"),
    "locker_b":      ("locker", "B", "imp"),
    "acc_wrapper_asrc":  ("acc_wrapper", "ASRC", "imp"),
    "acc_wrapper_eqdrc": ("acc_wrapper", "EQDRC", "imp"),
    "sed":           ("voice", "algo"),  # voice.algo == "sed" 时激活
    "vad":           ("voice", "algo"),  # voice.algo == "vad" 时激活
}

# ============================================================================
# 工具函数
# ============================================================================


def get_macro(*path):
    """从 MACROS 树中取值"""
    node = MACROS
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return 0
        node = node[p]
    return node if not isinstance(node, dict) else node.get("imp", 0)


def check_condition(cond):
    """检查单个条件"""
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


def parse_bits(bits_str):
    """解析 bits 字符串，返回 (high, low)"""
    bits_str = str(bits_str)
    if ":" in bits_str:
        parts = bits_str.split(":")
        return int(parts[0]), int(parts[1])
    return int(bits_str), int(bits_str)


def render_fields(fields):
    """渲染 bit field 表格"""
    if not fields:
        return ""

    lines = ["| Bits | Name | Access | Default | Description |",
             "|------|------|--------|---------|-------------|"]

    covered = set()
    parsed_fields = []

    for f in fields:
        high, low = parse_bits(f["bits"])
        for b in range(low, high + 1):
            covered.add(b)
        cond = f.get("condition")
        active = check_condition(cond)
        parsed_fields.append({
            "high": high,
            "low": low,
            "name": f["name"] if active else "reserved",
            "access": f.get("access", "R/W") if active else "R/W",
            "default": f.get("default", "0") if active else "0",
            "description": f.get("description", "") if active else "reserved",
        })

    parsed_fields.sort(key=lambda x: -x["high"])

    for f in parsed_fields:
        bits_str = f"{f['high']}:{f['low']}" if f["high"] != f["low"] else str(
            f["high"])
        lines.append(
            f"| {bits_str} | {f['name']} | {f['access']} | {f['default']} | {f['description']} |")

    return "\n".join(lines)


def is_active(mod_name):
    """模块是否启用"""
    gate = MODULE_GATE.get(mod_name)
    if gate is None:
        return False
    if mod_name in ("sed", "vad"):
        return get_macro("voice", "imp") and get_macro("voice", "algo") == mod_name
    return get_macro(*gate)


def strike(text):
    return f"~~{text}~~"


# ============================================================================
# 文档生成
# ============================================================================

def gen_base_table():
    lines = ["### Module Base Addresses", "",
             "| Module | Address |", "|--------|---------|"]
    for name, addr in sorted(MODULES.items(), key=lambda x: x[1]):
        addr_str = f"0x{addr:08X}"

        # 检查是否在 MODULE_GATE 中且未启用
        if name in MODULE_GATE:
            active = is_active(name)
            if not active:
                lines.append(f"| {strike(name)} | {strike(addr_str)} |")
                continue

        lines.append(f"| {name} | {addr_str} |")
    return "\n".join(lines)


def gen_clk_table():
    lines = ["### Clock Input Sources", ""]
    for group, inputs in CLK_INPUTS.items():
        lines.extend(["| Port | Driver |", "|------|--------|"])
        for suffix, driver in inputs.items():
            lines.append(f"| I_{group}_{suffix} | {driver} |")
        lines.append("")
    return "\n".join(lines)


def gen_macro_table():
    lines = ["### Macro Parameters", "",
             "| Path | Value |", "|------|-------|"]

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        else:
            lines.append(f"| {path} | {node} |")

    walk(MACROS)
    return "\n".join(lines)


def apply_rename_rules(reg_name, rules):
    """应用重命名规则"""
    if not rules:
        return reg_name
    for rule in rules:
        pattern = rule.get("pattern", "")
        replacement = rule.get("replacement", "")
        reg_name = re.sub(pattern, replacement, reg_name)
    return reg_name


def expand_register(reg, rename_rules=None, inst_id=None) -> list[RegEntry]:
    """展开寄存器定义 → RegEntry 列表"""
    rname = reg["name"]
    if rename_rules:
        rname = apply_rename_rules(rname, rename_rules)
    if inst_id:
        rname = re.sub(r'\{[A-Z_]+\}', inst_id, rname)

    fields = reg.get("fields", [])
    desc = reg.get("description", "")
    offsets = reg.get("offsets")
    entries = []

    if reg.get("voice_param"):
        voice_algo = get_macro("voice", "algo")
        if isinstance(voice_algo, str):
            algo_upper = voice_algo.upper()
            n = rname.replace("{VOICE}", algo_upper).replace("{TO_VOICE}", f"TO{algo_upper}")
            entries.append(RegEntry(n, reg.get("offset", 0), fields, desc))
    elif isinstance(offsets, dict):
        instances = reg.get("instances", list(offsets.keys()))
        conditions = reg.get("conditions", {})
        for idx in instances:
            if idx not in offsets:
                continue
            inst_cond = conditions.get(idx)
            if inst_cond and not check_condition(inst_cond):
                continue
            n = re.sub(r'\{[A-Z_]+\}', idx, rname)
            entries.append(RegEntry(n, offsets[idx], fields, desc))
    else:
        entries.append(RegEntry(rname, reg.get("offset", 0), fields, desc))

    return entries


def render_reg_entry(entry: RegEntry, lines: list):
    """渲染单个 RegEntry"""
    lines.append(f"#### {entry.name} @ 10'h{entry.offset:03X}")
    lines.append("")
    if entry.description:
        lines.append(entry.description)
        lines.append("")
    if entry.fields:
        lines.append(render_fields(entry.fields))
        lines.append("")


def check_module_condition(data):
    """检查模块级条件"""
    mod_cond = data.get("module_condition")
    if mod_cond and not check_condition(mod_cond):
        return False

    arch_cond = data.get("arch_condition")
    if arch_cond and not check_condition(arch_cond):
        return False

    return True


def resolve_voice_placeholder(name: str) -> str:
    """替换 {VOICE} 占位符"""
    voice_algo = get_macro("voice", "algo")
    if isinstance(voice_algo, str):
        algo_upper = voice_algo.upper()
        name = name.replace("{VOICE}", algo_upper)
        name = name.replace("{TO_VOICE}", f"TO{algo_upper}")
    return name


def parse_module_sections(data) -> list[ModuleSection]:
    """统一把 sections/instances/单例 解析为 ModuleSection 列表"""
    module_name = data["module"]
    sections = data.get("sections", {})
    instances = data.get("instances", {})
    registers = data.get("registers", [])
    has_parent = "parent_base" in data

    result = []

    if sections:
        for section_name, section_data in sections.items():
            title = resolve_voice_placeholder(f"{module_name}_{section_name}")
            result.append(ModuleSection(
                title=title,
                gate_key=title.lower(),
                registers=section_data.get("registers", []),
                has_parent=has_parent,
            ))
    elif instances:
        for inst_id, inst_cfg in instances.items():
            title = resolve_voice_placeholder(f"{module_name}_{inst_id}")
            cond = inst_cfg.get("condition") if inst_cfg else None
            result.append(ModuleSection(
                title=title,
                gate_key=title.lower(),
                registers=registers,
                rename_rules=inst_cfg.get("rename_rules") if inst_cfg else None,
                has_parent=has_parent,
                condition=cond,
                inst_id=inst_id,
            ))
    else:
        title = resolve_voice_placeholder(module_name)
        result.append(ModuleSection(
            title=title,
            gate_key=title.lower(),
            registers=registers,
            has_parent=has_parent,
        ))

    return result


def gen_module_regs(yaml_path):
    """生成单个模块的寄存器章节"""
    data = yaml.safe_load(yaml_path.read_text())
    module_active = check_module_condition(data)
    sections = parse_module_sections(data)
    all_lines = []

    for sec in sections:
        lines = [f"### {sec.title}", ""]

        if not module_active:
            lines.append(f"*Not present in {CHIP}*")
        elif sec.condition and not check_condition(sec.condition):
            lines.append(f"*Not present in {CHIP}*")
        elif sec.gate_key in MODULE_GATE and not is_active(sec.gate_key):
            lines.append(f"*Not present in {CHIP}*")
        elif not sec.has_parent and sec.gate_key not in MODULES and "_" in sec.gate_key:
            lines.append(f"*Not defined in MODULES*")
        else:
            for reg in sec.registers:
                for entry in expand_register(reg, sec.rename_rules, sec.inst_id):
                    render_reg_entry(entry, lines)

        all_lines.extend(lines)
        all_lines.append("")

    return "\n".join(all_lines)


# ============================================================================
# 输出转换 (pandoc)
# ============================================================================

def pandoc_convert(md_path: Path, out_path: Path) -> bool:
    """使用 pandoc 转换 md → html/pdf，返回是否成功"""
    cmd = ["pandoc", str(md_path), "-o", str(out_path), "--standalone"]
    if out_path.suffix == ".html":
        cmd.extend(["--metadata", f"title={CHIP} Audio Spec"])
    elif out_path.suffix == ".pdf":
        cmd.extend(["--pdf-engine=wkhtmltopdf"])
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


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

    lines = [
        f"# {CHIP} Audio Register Specification",
        "",
        gen_base_table(),
        "",
        "---",
        "",
        "## Register Details",
        "",
    ]

    for yaml_path in sorted(modules_dir.glob("*.yaml")):
        lines.extend([gen_module_regs(yaml_path), ""])

    lines.extend(["---", "", gen_clk_table(), "---", "", gen_macro_table()])

    md_path = output_dir / f"{FILE}.md"
    md_path.write_text("\n".join(lines))
    print(f"输出: {md_path}")

    html_path = output_dir / f"{FILE}.html"
    if pandoc_convert(md_path, html_path):
        print(f"输出: {html_path}")
    else:
        print("HTML 生成失败，请安装 pandoc")

    pdf_path = output_dir / f"{FILE}.pdf"
    if pandoc_convert(md_path, pdf_path):
        print(f"输出: {pdf_path}")
    else:
        print("PDF 跳过（需要: sudo apt install wkhtmltopdf）")


if __name__ == "__main__":
    main()
