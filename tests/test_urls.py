import pytest

from ytreel.urls import InvalidYouTubeURL, extract_video_id, timestamp_url

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abcdef",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&t=42s",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        "  https://youtu.be/dQw4w9WgXcQ  ",
        "<https://youtu.be/dQw4w9WgXcQ>",
        "dQw4w9WgXcQ",
    ],
)
def test_extract(raw):
    assert extract_video_id(raw) == VID


@pytest.mark.parametrize("raw", ["", "   ", "https://example.com/watch?v=abc", "not a url"])
def test_rejects_garbage(raw):
    with pytest.raises(InvalidYouTubeURL):
        extract_video_id(raw)


def test_timestamp_url():
    assert timestamp_url(VID, 125.7).endswith("&t=125s")
