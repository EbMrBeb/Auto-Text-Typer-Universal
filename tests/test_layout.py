from auto_essay_typer.layout import Hand, key_info, needs_shift, neighbors, same_finger, same_hand


def test_home_row_keys_have_expected_hands():
    assert key_info("a").hand is Hand.LEFT
    assert key_info("l").hand is Hand.RIGHT


def test_uppercase_maps_to_base_key_and_requires_shift():
    info = key_info("A")
    assert info.row == key_info("a").row
    assert info.finger == key_info("a").finger
    assert needs_shift("A") is True
    assert needs_shift("a") is False


def test_shifted_symbols_map_to_their_base_key():
    assert key_info("!").finger == key_info("1").finger
    assert needs_shift("!") is True


def test_neighbors_are_physically_adjacent():
    result = set(neighbors("s"))
    assert {"a", "d", "w", "x"} <= result
    assert "p" not in result


def test_neighbors_preserve_shift_state():
    assert all(c.isupper() for c in neighbors("S"))
    assert "D" in neighbors("S")


def test_same_finger_detects_vertical_pairs():
    assert same_finger("e", "d") is True
    assert same_finger("e", "k") is False


def test_same_hand():
    assert same_hand("q", "z") is True
    assert same_hand("q", "p") is False


def test_unknown_characters_degrade_gracefully():
    assert key_info("é") is None
    assert neighbors("é") == []
    assert same_finger("é", "a") is False


def test_space_is_a_thumb_key():
    assert key_info(" ").finger == 5
