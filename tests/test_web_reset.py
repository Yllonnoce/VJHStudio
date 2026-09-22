"""The Reset button on the Generate page (both modes)."""

from pathlib import Path

APP_JS = (Path(__file__).parents[1] / "vjhstudio" / "web" / "static" / "js" / "app.js").read_text()


async def test_reset_button_is_on_both_generate_modes(client):
    for path in ("/generate/image", "/generate/video"):
        r = await client.get(path)
        assert r.status_code == 200
        assert 'id="generate-reset"' in r.text and '@click="resetForm()"' in r.text


def test_reset_clears_everything_the_user_typed_and_refetches_the_panel():
    body = APP_JS.split("resetForm() {", 1)[1].split("setMode(mode) {", 1)[0]
    for line in (
        "this.fields = vjhEmptyFields();",
        "this.finalPrompt = '';",
        "this.polishJson = '';",
        "this.promptId = '';",
        "this.refs = [];",
        "this._clearDraft();",
        "/hx/model-options?mode=",
    ):
        assert line in body, line
