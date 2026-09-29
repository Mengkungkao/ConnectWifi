from connectwifi import network


def test_split_fields_unescapes_colons_and_backslashes():
    assert network.split_fields(r"*:Cafe\:Guest:72:WPA2") == ["*", "Cafe:Guest", "72", "WPA2"]
    assert network.split_fields(r" :back\\slash:5:") == [" ", "back\\slash", "5", ""]


def test_parse_networks_keeps_one_row_per_ssid_with_the_best_signal():
    output = "\n".join([
        " :Home:40:WPA2",
        "*:Home:35:WPA2",      # in use, but on the weaker access point
        " :Home:81:WPA2 WPA3",
        " :Cafe\\:Guest:66:",
        " ::90:WPA2",          # hidden: no SSID to offer
        " :Bad:x:WPA2",
        " :Loud:130:WPA2",
    ])
    found = network.parse_networks(output)
    assert [n.ssid for n in found] == ["Home", "Loud", "Cafe:Guest", "Bad"]
    home = found[0]
    assert home.active and home.signal == 81 and home.security == "WPA2 WPA3"
    assert found[1].signal == 100          # clamped
    assert found[2].is_open
    assert found[3].signal == 0


def test_parse_networks_sorts_in_use_first_then_strongest():
    output = " :A:50:WPA2\n*:B:20:WPA2\n :C:90:WPA2\n"
    assert [n.ssid for n in network.parse_networks(output)] == ["B", "C", "A"]


def test_wifi_device_skips_p2p_bluetooth_and_loopback():
    # Real output from the Raspberry Pi, Bluetooth MAC and all.
    output = "lo:loopback\nA4\\:F6\\:E8\\:2D\\:26\\:C8:bt\np2p-dev-wlan0:wifi-p2p\nwlan0:wifi\n"
    assert network.wifi_device(output) == "wlan0"
    assert network.wifi_device("eth0:ethernet\n") == ""


def test_active_ssid():
    assert network.active_ssid("no:Other\nyes:Cafe\\:Guest\n") == "Cafe:Guest"
    assert network.active_ssid("no:Other\nyes:\n") == "(hidden)"
    assert network.active_ssid("no:Other\n") == ""


def test_ipv4_address_drops_the_prefix_length():
    assert network.ipv4_address("IP4.ADDRESS[1]:192.168.0.33/24\n") == "192.168.0.33"
    assert network.ipv4_address("IP4.ADDRESS[1]:\n") == ""


def test_ssid_listed_matches_whole_escaped_names():
    output = "Cafe\\:Guest\nHome\n"
    assert network.ssid_listed(output, "Cafe:Guest")
    assert not network.ssid_listed(output, "Cafe")


def test_scan_permitted():
    yes = "org.freedesktop.NetworkManager.network-control:yes\norg.freedesktop.NetworkManager.wifi.scan:yes\n"
    auth = "org.freedesktop.NetworkManager.wifi.scan:auth\n"
    assert network.scan_permitted(yes)
    assert not network.scan_permitted(auth)
    assert not network.scan_permitted("")


def test_list_command_always_says_whether_to_sweep():
    # nmcli's default, --rescan auto, sweeps whenever the cache is over 30s
    # old: a status poll that left it out would keep the radio scanning.
    assert network.list_command("wlan0", "no")[-2:] == ["--rescan", "no"]
    assert network.list_command("wlan0", "no")[-4:-2] == ["ifname", "wlan0"]
    assert "ifname" not in network.list_command("", "yes")


def test_connect_command():
    assert network.connect_command("Home", "secret99", "wlan0", hidden=False) == [
        "nmcli", "device", "wifi", "connect", "Home", "password", "secret99", "ifname", "wlan0"]
    assert network.connect_command("Lab", "", "", hidden=True) == [
        "nmcli", "device", "wifi", "connect", "Lab", "hidden", "yes"]
