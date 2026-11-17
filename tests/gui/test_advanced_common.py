

# --- FlowLayout --------------------------------------------------------------------------------------------


def _flow(qtbot, count: int, width: int):
    from PySide6.QtWidgets import QPushButton, QWidget

    from auto_essay_typer.gui.advanced_common import FlowLayout

    host = QWidget()
    qtbot.addWidget(host)
    layout = FlowLayout(host, spacing=10)
    buttons = []
    for number in range(count):
        button = QPushButton(f"Button {number}")
        button.setFixedSize(100, 30)
        layout.addWidget(button)
        buttons.append(button)
    host.resize(width, layout.heightForWidth(width))
    host.show()
    return host, layout, buttons


def test_a_flow_layout_keeps_items_on_one_line_when_they_fit(qtbot):
    _host, layout, buttons = _flow(qtbot, 3, 400)
    assert {button.y() for button in buttons} == {0}
    assert layout.heightForWidth(400) == 30


def test_a_flow_layout_wraps_when_they_do_not_fit_and_never_exceeds_the_width(qtbot):
    host, layout, buttons = _flow(qtbot, 4, 250)  # two 100 px buttons plus a gap per line
    assert sorted({button.y() for button in buttons}) == [0, 40]
    assert layout.heightForWidth(250) == 70
    assert all(button.geometry().right() < 250 for button in buttons)
    assert host.layout() is layout


def test_a_flow_layouts_minimum_is_its_widest_item_not_their_sum(qtbot):
    _host, layout, _buttons = _flow(qtbot, 4, 500)
    assert layout.minimumSize().width() == 100
