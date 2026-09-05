from app.risk.sizing import position_size


def test_position_size():
    assert position_size(10_000, 100, 95, 0.01) == 20.0
    assert position_size(10_000, 100, 100, 0.01) == 0.0
    assert position_size(50_000, 200, 190, 0.02) == 100.0