"""Mock VPP-1 signal provider."""


def get_mock_vpp1_raw_signal() -> dict:
    return {
        "eventCode": "DR_EVENT",
        "windowStart": "18:00",
        "windowEnd": "19:00",
        "reductionTargetKw": 0.5,
        "tariff": "high",
    }
