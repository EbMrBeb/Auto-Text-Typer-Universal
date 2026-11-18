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


def test_tilde_resolves_to_the_backtick_key():
    # Regression: _SHIFTED mapped "~" to a key absent from _BASE, raising KeyError.
    info = key_info("~")
    assert info is not None
    assert info.finger == 0
    assert needs_shift("~") is True


def test_backtick_is_modelled():
    assert key_info("`") is not None
    assert needs_shift("`") is False


def test_backtick_sits_left_of_the_number_one():
    assert key_info("`").x < key_info("1").x


def test_number_row_geometry_is_unshifted_by_the_backtick():
    # 'q' must remain between '1' and '2' horizontally.
    assert key_info("1").x < key_info("q").x < key_info("2").x


def test_every_shifted_symbol_resolves_to_a_real_key():
    # Guards the whole class of bug: any _SHIFTED entry pointing at a key
    # missing from _BASE would crash key_info.
    for symbol in "!@#$%^&*()_+{}|:\"<>?~":
        assert key_info(symbol) is not None, f"{symbol!r} does not resolve"
        assert needs_shift(symbol) is True
