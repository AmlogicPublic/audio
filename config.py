"""
Audio Spec 配置 - 人类编辑区
"""

CHIP = "C5"

# ============================================================================
# 1. 宏定义（对应 macro.hpp，0=off, 1=on, 数值=参数）
# ============================================================================
MACROS = {
    #  soundbar(A series and some T series need 32 ch support)

    # ── 输入设备 ──
    "tdmin": {
        "A": {"imp": 1, "pad_num": 2},
        "B": {"imp": 0, "pad_num": 2},
        "C": {"imp": 0, "pad_num": 8},
        "D": {"imp": 0, "pad_num": 8},
        "LB_A": {"imp": 1, "pad_num": 2},
        "LB_B": {"imp": 0, "pad_num": 8},
    },
    "spdifin": {
        "A": {"imp": 0},
        "B": {"imp": 0},
        "LB_A": {"imp": 0},
        "LB_B": {"imp": 0},
    },
    "pdm": {
        "A": {"imp": 0, "chan_num": 8},
        "B": {"imp": 1, "chan_num": 8},
    },
    "hdmirx": {  # TDM/SPDIF/DSD input interface
        "A": {"imp": 0, "dsdin_imp": 0},
        "B": {"imp": 0, "dsdin_imp": 0},
    },
    "earcrx": {"imp": 0},
    "atv": {"imp": 0},

    # ── 输出设备 ──
    "tdmout": {
        "A": {"imp": 1, "pad_num": 2},
        "B": {"imp": 1, "pad_num": 2},
        "C": {"imp": 0, "pad_num": 8},
        "D": {"imp": 0, "pad_num": 8},
    },
    "spdifout": {
        "A": {"imp": 0, "ch16": 1},  # else 8ch
        "B": {"imp": 0, "ch16": 1},  # else 8ch
    },
    "hdmi_dp_tx": {
        "A": {"imp": 0},
        "B": {"imp": 0}
    },
    "earctx": {"imp": 0},

    # ── 输入处理 ──
    "loopback": {
        "A": {"imp": 1, "ch": 4},   # 4/8/16, 0=off
        "B": {"imp": 0, "ch": 0},
    },
    "resample": {
        "A": {"imp": 1, "dw": 24, "chnum": 8},
        "B": {"imp": 0, "dw": 24, "chnum": 32},
        "C": {"imp": 0, "dw": 24, "chnum": 32},
    },

    # ── 输出处理 ──
    "eqdrc": {
        "imp": 0,
        "ch_in": 0,
        "static_coeff": 0  # TV: use dynamic coeff; STB: use static coeff
    },
    "mixer": {"imp": 1},

    # ── DMA ──
    # size = depth*64b         B,C,D need to have the same depth
    "toddr": {
        "A": {"imp": 1, "fifo_depth": 128},
        "B": {"imp": 1, "fifo_depth": 128},
        "C": {"imp": 0, "fifo_depth": 128},
        "D": {"imp": 0, "fifo_depth": 128},
        "E": {"imp": 0, "fifo_depth": 128},
        "ch_sync_depth": 16,  # TV: 32; STB: 16 (no channel sync for FRDDR)
    },
    "frddr": {
        "A": {"imp": 1, "fifo_depth": 128},
        "B": {"imp": 1, "fifo_depth": 128},
        "C": {"imp": 0, "fifo_depth": 128},
        "D": {"imp": 0, "fifo_depth": 128},
        "E": {"imp": 0, "fifo_depth": 128},
    },
    "axi_irq_sync": {"imp": 1},

    # ── MISC ──
    "voice": {
        "imp": 1,
        "algo": "vad",  # vad(传统谱熵检测)/sed(神经网络特征检测)
        "in_ao": 1  # 放在ao还是ee
    },
    "aocdec": {"imp": 1},
    "locker": {"A": {"imp": 1}, "B": {"imp": 0}},
    "pcpd_mon": {"A": {"imp": 1}, "B": {"imp": 0}},
    "pwr_domain": {"imp": 1},
    "ddr_arb": {"imp": 0},
}

# ============================================================================
# 2. 模块基地址（完整地址，模块是最小单位）
# ============================================================================
MODULES = {
    # 主控制模块
    "audio_top_ee":        0xFFAE0000,
    "audio_top_ao":        0xFFAE1000,
    
    # Voice模块 (根据宏配置使用不同算法实现)
    "sed":                 0xFFAC0000,  # SED算法(神经网络)
    "vad":                 0xFE331800,  # VAD算法(传统谱熵)
    
    # PDM 实例
    "pdm_a":               0xFFAE2000,
    "pdm_b":               0xFFAE2400,
    
    # Resample 实例
    "resample_a":          0xFFAE3000,
    "resample_b":          0xFFAE3400,
    "resample_c":          0xFFAE3800,
    
    # EQ/DRC
    "eq_drc":              0xFFAE4000,
    
    # eARC TX
    "earctx_cmdc":         0xFFAE5000,
    "earctx_dmac":         0xFFAE5400,
    "earctx_top":          0xFFAE5600,
    
    # eARC RX
    "earcrx_cmdc":         0xFFAE5800,
    "earcrx_dmac":         0xFFAE5C00,
    "earcrx_top":          0xFFAE5E00,
    
    # Locker 实例
    "locker_a":            0xFFAE6000,
    "locker_b":            0xFFAE6400,
    
    # Wrappers
    "asrc_wrapper":        0xFFAE8400,
    "eqdrc_wrapper":       0xFFAE8800,
}


# ============================================================================
# 模块使用决策
# ============================================================================
def get_active_voice_module():
    """根据宏配置返回当前使用的voice模块"""
    voice_cfg = MACROS.get("voice", {})
    if not voice_cfg.get("imp"):
        return None
    
    algo = voice_cfg.get("algo", "sed")
    return algo  # 返回 "sed" 或 "vad"

# ============================================================================
# 3. 时钟输入源
# ============================================================================
CLK_INPUTS = {
    "other_pll": {
        "a": "Oscin_clk(24M)",
        "b": "Hifi0_pll_clk",
        "c": "1'b0",
        "d": "Cts_rtc_clk",
        "e": "1'b0",
        "f": "Fclk_div3",
        "g": "Fclk_div4",
        "h": "Fclk_div5",
    },
    "slv": {
        "a": "Mod_audio_slv_sclk0",
        "b": "Mod_audio_slv_sclk1",
        "c": "1'b0",
        "d": "1'b0",
        "e": "1'b0",
        "f": "1'b0",
        "g": "Wifi_beacon_i",
        "h": "1'b0",
        "i": "1'b0",
        "j": "Acodec_ADC",
    },
}
