from greeter import whisper


def test_whisper():
    assert whisper("Ada") == "hello, ada..."
    assert whisper("BOB") == "hello, bob..."
