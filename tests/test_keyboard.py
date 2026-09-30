import struct

from connectwifi import keyboard

# A full-size USB keyboard, and the Raspberry Pi's event0: it claims a kbd
# handler, but has no letter row (both from /proc/bus/input/devices).
USB_KEYBOARD_BITS = "1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe"
PI_EVENT0_BITS = ("ffffc000000000 3ff 0 400000320fc200 40830c900000000 0 210300 "
                  "49d2c040ec00 1e378000000000 8010000010000000")


def bitmap_words(codes, word_bits):
    """Print a key bitmap the way the kernel does, for a given long width."""
    value = 0
    for code in codes:
        value |= 1 << code
    words = []
    while value:
        words.append(value & ((1 << word_bits) - 1))
        value >>= word_bits
    return " ".join(f"{word:x}" for word in reversed(words))


def test_has_letter_keys():
    word_bits = struct.calcsize("l") * 8
    if word_bits == 64:
        assert keyboard.has_letter_keys(USB_KEYBOARD_BITS)
        assert not keyboard.has_letter_keys(PI_EVENT0_BITS)
    letters = keyboard.LETTER_KEYCODES + (28, 57)
    assert keyboard.has_letter_keys(bitmap_words(letters, word_bits))
    assert not keyboard.has_letter_keys(bitmap_words(letters[:-5], word_bits))
    assert not keyboard.has_letter_keys("")
    assert not keyboard.has_letter_keys("zz")


def test_keyboard_event_names_keeps_only_real_keyboards():
    word_bits = struct.calcsize("l") * 8
    full = bitmap_words(keyboard.LETTER_KEYCODES + (28,), word_bits)
    remote = bitmap_words((103, 108, 28, 1), word_bits)
    devices = (
        'N: Name="vc4-hdmi"\nH: Handlers=kbd event0 \nB: KEY=' + remote + "\n\n"
        'N: Name="USB Keyboard"\nH: Handlers=sysrq kbd leds event1 \nB: KEY=' + full + "\n\n"
        'N: Name="Mouse"\nH: Handlers=mouse0 event2 \nB: KEY=70000 0 0 0 0\n'
    )
    assert keyboard.keyboard_event_names(devices) == ["event1"]


def test_key_action():
    assert keyboard.key_action(keyboard.KEY_ENTER, False) == "submit"
    assert keyboard.key_action(keyboard.KEY_KPENTER, False) == "submit"
    assert keyboard.key_action(keyboard.KEY_ESC, False) == "cancel"
    assert keyboard.key_action(keyboard.KEY_SPACE, True) == ("char", " ")
    assert keyboard.key_action(30, False) == ("char", "a")
    assert keyboard.key_action(30, True) == ("char", "A")
    assert keyboard.key_action(2, True) == ("char", "!")
    assert keyboard.key_action(59, False) is None      # F1


def test_reader_tracks_shift_and_ignores_releases():
    seen = []
    reader = keyboard.KeyboardReader()
    reader._callback = seen.append
    reader.handle_event(keyboard.EV_KEY, 42, keyboard.KEY_PRESS)      # shift down
    reader.handle_event(keyboard.EV_KEY, 30, keyboard.KEY_PRESS)
    reader.handle_event(keyboard.EV_KEY, 42, keyboard.KEY_RELEASE)    # shift up
    reader.handle_event(keyboard.EV_KEY, 30, keyboard.KEY_PRESS)
    reader.handle_event(keyboard.EV_KEY, 30, keyboard.KEY_REPEAT)
    reader.handle_event(keyboard.EV_KEY, 30, keyboard.KEY_RELEASE)
    reader.handle_event(0x02, 0, 1)                                   # EV_REL: mouse
    assert seen == [("char", "A"), ("char", "a"), ("char", "a")]


def test_restart_does_not_leave_the_old_reader_running():
    reader = keyboard.KeyboardReader()
    reader.start(lambda action: None)
    first = reader._stop_event
    reader.start(lambda action: None)
    assert first.is_set() and not reader._stop_event.is_set()
    reader.stop()


def test_sdk_reader_maps_keys_to_the_apps_actions():
    """The MFruit App SDK's keys (through MFruit OS's key hub) become the same
    actions the app always handled."""
    from mfruit_sdk.keys import DOWN, REPEAT, UP, KeyEvent

    actions = []
    reader = keyboard.SdkKeyboardReader()
    reader._callback = actions.append
    for event in (KeyEvent("key", "up", DOWN, 103), KeyEvent("key", "down", REPEAT, 108),
                  KeyEvent("key", "enter", DOWN, 28), KeyEvent("key", "enter", REPEAT, 28),
                  KeyEvent("key", "escape", DOWN, 1), KeyEvent("key", "backspace", DOWN, 14),
                  KeyEvent("key", "space", DOWN, 57), KeyEvent("char", "A", DOWN, 30),
                  KeyEvent("key", "enter", UP, 28), KeyEvent("key", "home", DOWN, 102)):
        reader.handle(event)
    assert actions == ["up", "down", "submit", "cancel", "backspace", ("char", " "),
                       ("char", "A")]


def test_sdk_reader_asks_for_this_apps_keys():
    reader = keyboard.SdkKeyboardReader()
    assert reader.app_id == "connectwifi"
