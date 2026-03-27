"""
Audio Spec 配置
"""

CHIP = "A10"

# ============================================================================
# 模块树 - 层级结构 + 配置参数统一定义
# ============================================================================
MODULE_TREE = {
    "audio_ao_top": {
        "_addr": 0xFFAE1000,
        "audio_top_ao": {"imp": 1},  # audio_top_ao.yaml
        "Input": {
            "pdm": {
                "A": {"_addr": 0xFFAE2000, "imp": 1, "chan_num": 8},
                "B": {"_addr": 0xFFAE2400, "imp": 0, "chan_num": 8},
            },
        },
        "Voice": {
            "sed": {"_addr": 0xFFAC0000, "imp": 1},
            "vad": {"_addr": 0xFE331800, "imp": 0},
        },
    },
    "audio_ee_top": {
        "_addr": 0xFFAE0000,
        "audio_top_ee": {"imp": 1},  # audio_top_ee.yaml
        "Input": {
            "tdmin": {
                "A":    {"imp": 1, "pad_num": 4},
                "B":    {"imp": 1, "pad_num": 4},
                "C":    {"imp": 1, "pad_num": 8},
                "D":    {"imp": 0, "pad_num": 8},
                "LB_A": {"imp": 1, "pad_num": 8},
                "LB_B": {"imp": 0, "pad_num": 8},
            },
            "spdifin":    {"A": {"imp": 1}, "B": {"imp": 0}},
            "spdifin_lb": {"A": {"imp": 0}, "B": {"imp": 0}},
            "pdm": {
                "A": {"_addr": 0xFFAE2000, "imp": 0, "chan_num": 8},
                "B": {"_addr": 0xFFAE2400, "imp": 0, "chan_num": 8},
            },
            "frhdmirx":   {"A": {"imp": 0}, "B": {"imp": 0}},
            "fratv":      {"imp": 0},
            "earcrx": {
                "CMDC": {"_addr": 0xFFAE5800, "imp": 1},
                "DMAC": {"_addr": 0xFFAE5C00, "imp": 1},
                "TOP":  {"_addr": 0xFFAE5E00, "imp": 1},
            },
        },
        "Output": {
            "tdmout": {
                "A": {"imp": 1, "pad_num": 4},
                "B": {"imp": 1, "pad_num": 4},
                "C": {"imp": 1, "pad_num": 13},
                "D": {"imp": 0, "pad_num": 8},
            },
            "spdifout": {
                "A": {"imp": 1, "ch_support": "up to 16 channels", "ch_feature": "Multi-channel support up to 16 channels"},
                "B": {"imp": 1, "ch_support": "up to 16 channels", "ch_feature": "Multi-channel support up to 16 channels"},
            },
            "earctx": {
                "CMDC": {"_addr": 0xFFAE5000, "imp": 1},
                "DMAC": {"_addr": 0xFFAE5400, "imp": 1},
                "TOP":  {"_addr": 0xFFAE5600, "imp": 1},
            },
            "tohdmi_dp_tx": {
                "A": {"imp": 1},
                "B": {"imp": 0},
            },
        },
        "InputProcessing": {
            "resample": {
                "A": {"_addr": 0xFFAE3000, "imp": 1, "dw": 24, "chnum": 32},
                "B": {"_addr": 0xFFAE3400, "imp": 1, "dw": 24, "chnum": 16},
                "C": {"_addr": 0xFFAE3800, "imp": 0, "dw": 24, "chnum": 2},
            },
            "resample_id": {"A": {"imp": 1}, "B": {"imp": 1}, "C": {"imp": 0}},
            "loopback":    {"A": {"imp": 1, "ch": 16}, "B": {"imp": 0, "ch": 0}},
        },
        "OutputProcessing": {
            "mixer":  {"A": {"imp": 1}},
            "eq_drc": {"_addr": 0xFFAE4000, "imp": 1, "ch": 32, "static": 1, "arch": "reg", "arch_desc": "coefficients in dedicated registers"},
        },
        "IOProcessing": {
            "tdm_dat_pad": {"imp": 1},
            "toacodec": {"imp": 0},
        },
        "Mem2MemProcessing": {
            "acc_wrapper": {
                "ASRC":  {"_addr": 0xFFAE8400, "imp": 0},
                "EQDRC": {"_addr": 0xFFAE8800, "imp": 0},
            },
        },
        "Voice": {
            "sed": {"_addr": 0xFFAC0000, "imp": 0},
            "vad": {"_addr": 0xFE331800, "imp": 0},
        },
        "DMA": {
            "toddr": {
                "A": {"imp": 1, "fifo_depth": 256},
                "B": {"imp": 1, "fifo_depth": 128},
                "C": {"imp": 1, "fifo_depth": 128},
                "D": {"imp": 1, "fifo_depth": 128},
                "E": {"imp": 0, "fifo_depth": 128},
            },
            "frddr": {
                "A": {"imp": 1, "fifo_depth": 256},
                "B": {"imp": 1, "fifo_depth": 128},
                "C": {"imp": 1, "fifo_depth": 128},
                "D": {"imp": 1, "fifo_depth": 128},
                "E": {"imp": 1, "fifo_depth": 128},
            },
            "ddr_arb": {"imp": 0},
        },
        "Misc": {
            "locker":   {
                "A": {"_addr": 0xFFAE6000, "imp": 1}, 
                "B": {"_addr": 0xFFAE6400, "imp": 0}
                },
            "pcpd_mon": {"A": {"imp": 0}, "B": {"imp": 0}},
        },
    },
}

# ============================================================================
# 时钟输入源
# ============================================================================
CLK_INPUTS = {
    "other_pll": {
        "a": "Oscin_clk(24M)",
        "b": "Hifi0_pll_clk",
        "c": "Hifi1_pll_clk",
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
        "f": "Hdmirx_aud_sck",
        "g": "Wifi_beacon_i",
        "h": "1'b0",
        "i": "1'b0",
        "j": "1'b0",
    },
}
