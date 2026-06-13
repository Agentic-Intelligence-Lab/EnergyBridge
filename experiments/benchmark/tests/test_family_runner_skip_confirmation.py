from experiments.benchmark.family_runner import _requested_skip_devices


def test_requested_skip_devices_returns_only_explicit_true_flags() -> None:
    actions = {
        "washer_skip": True,
        "dishwasher_skip": False,
        "dryer_skip": None,
    }
    assert _requested_skip_devices(actions) == ["washer"]
