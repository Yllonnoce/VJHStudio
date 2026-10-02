"""Request models and pure task builders for music & SFX, speech and 3D."""

import pytest
from pydantic import ValidationError

from vjhstudio.runware import tasks_media as T
from vjhstudio.schemas import media as M

ACE = {
    "constraints": {
        "fields": {
            "positivePrompt": {"required": True, "min": 2, "max": 3000},
            "duration": {"type": "float", "min": 30, "max": 300, "default": 60},
            "seed": {"type": "integer"},
            "settings.lyrics": {"type": "string"},
            "settings.bpm": {"type": "integer", "min": 30, "max": 300},
        }
    }
}
MINIMAX_MUSIC = {
    "constraints": {
        "fields": {
            "positivePrompt": {"required": True},
            "negativePrompt": {},
            "seed": {},
            "settings.instrumental": {"type": "boolean"},
            "settings.lyrics": {"type": "string"},
        }
    }
}
XAI = {
    "constraints": {
        "fields": {
            "speech.text": {"required": True},
            "speech.voice": {"values": ["eve", "luna"], "default": "eve"},
            "speech.language": {"values": ["en", "fr"]},
        }
    }
}
INWORLD = {
    "constraints": {
        "fields": {
            "speech.text": {"required": True, "max": 2000},
            "speech.voice": {"values": ["Hank"]},
            "speech.speed": {"type": "float", "min": 0.5, "max": 1.5, "default": 1},
            "settings.textNormalization": {"type": "boolean"},
        }
    }
}
HUNYUAN = {
    "constraints": {
        "fields": {"positivePrompt": {"max": 200}, "settings.pbr": {"type": "boolean"}},
        "inputs": {"image": {"required": False}},
    }
}
TRIPO = {
    "constraints": {
        "fields": {"positivePrompt": {}, "negativePrompt": {}, "seed": {}},
        "inputs": {"images": {"required": False, "min_items": 1, "max_items": 4}},
    }
}
BARE = {"constraints": {}}


def audio(**kw):
    return M.AudioRequest(
        **{
            "project_id": 1,
            "model": "runware:ace-step@v1.5-turbo",
            "prompt": "upbeat synthwave",
            **kw,
        }
    )


def speech(**kw):
    return M.SpeechRequest(**{"project_id": 1, "model": "xai:tts@0", "text": "Hello there.", **kw})


def model3d(**kw):
    return M.Model3DRequest(**{"project_id": 1, "model": "tencent:hunyuan-3d@3.1-rapid", **kw})


# ---- music & sound effects -------------------------------------------------------------
def test_audio_task_shape():
    t = T.build_audio_task(audio(duration=45, seed=7), "u", ACE)
    assert t == {
        "taskType": "audioInference",
        "taskUUID": "u",
        "model": "runware:ace-step@v1.5-turbo",
        "outputType": "URL",
        "outputFormat": "MP3",
        "includeCost": True,
        "positivePrompt": "upbeat synthwave",
        "duration": 45,
        "seed": 7,
    }


def test_audio_duration_is_clamped_to_the_model_and_left_out_when_not_asked_for():
    assert T.build_audio_task(audio(duration=5), "u", ACE)["duration"] == 30
    assert T.build_audio_task(audio(duration=900), "u", ACE)["duration"] == 300
    assert T.build_audio_task(audio(duration=61.5), "u", ACE)["duration"] == 61.5
    assert "duration" not in T.build_audio_task(audio(), "u", ACE)  # the model's own default
    # MiniMax Music takes no length at all: a posted one is not sent
    assert "duration" not in T.build_audio_task(audio(duration=60), "u", MINIMAX_MUSIC)


def test_audio_optional_fields_only_when_the_model_lists_them():
    req = audio(
        lyrics="la la la la la",
        instrumental=False,
        negative_prompt="no drums",
        settings={"bpm": 120, "keyScale": "C"},
    )
    ace = T.build_audio_task(req, "u", ACE)
    assert ace["settings"] == {"bpm": 120, "lyrics": "la la la la la"}
    assert "negativePrompt" not in ace
    mm = T.build_audio_task(req, "u", MINIMAX_MUSIC)
    assert mm["settings"] == {"lyrics": "la la la la la"}  # "with vocals" is never sent as false
    assert mm["negativePrompt"] == "no drums"
    # nothing harvested: everything asked for is sent and RunWare's rejection sorts it out
    bare = T.build_audio_task(req, "u", BARE)
    assert bare["settings"] == {
        "bpm": 120,
        "keyScale": "C",
        "lyrics": "la la la la la",
    }
    assert "settings" not in T.build_audio_task(audio(), "u", ACE)


def test_extra_json_merges_last_but_cannot_change_the_task_identity():
    t = T.build_audio_task(
        audio(extra_json={"steps": 8, "taskType": "x", "model": "y", "taskUUID": "z"}), "u", ACE
    )
    assert t["steps"] == 8
    assert (t["taskType"], t["model"], t["taskUUID"]) == (
        "audioInference",
        "runware:ace-step@v1.5-turbo",
        "u",
    )


# ---- speech ---------------------------------------------------------------------------
def test_speech_task_shape():
    t = T.build_speech_task(speech(voice="luna", language="en", output_format="WAV"), "u", XAI)
    assert t == {
        "taskType": "audioInference",
        "taskUUID": "u",
        "model": "xai:tts@0",
        "outputType": "URL",
        "outputFormat": "WAV",
        "includeCost": True,
        "speech": {"text": "Hello there.", "voice": "luna", "language": "en"},
    }
    assert "positivePrompt" not in t


def test_speech_optional_parts_follow_the_model():
    # xAI lists no speed; Inworld does, within 0.5–1.5, and no language
    assert "speed" not in T.build_speech_task(speech(speed=1.2), "u", XAI)["speech"]
    t = T.build_speech_task(
        speech(speed=3, language="en", voice="Hank", settings={"textNormalization": True}),
        "u",
        INWORLD,
    )
    assert t["speech"] == {"text": "Hello there.", "voice": "Hank", "speed": 1.5}
    assert t["settings"] == {"textNormalization": True}
    # a voice that is not on the harvested list is still sent as typed
    assert T.build_speech_task(speech(voice="nova"), "u", XAI)["speech"]["voice"] == "nova"
    assert "voice" not in T.build_speech_task(speech(), "u", XAI)["speech"]


# ---- 3D ---------------------------------------------------------------------------------
def test_3d_task_from_text():
    t = T.build_3d_task(
        model3d(prompt="a brass telescope", settings={"pbr": True, "quad": True}), "u", {}, HUNYUAN
    )
    assert t == {
        "taskType": "3dInference",
        "taskUUID": "u",
        "model": "tencent:hunyuan-3d@3.1-rapid",
        "outputType": "URL",
        "outputFormat": "GLB",
        "includeCost": True,
        "positivePrompt": "a brass telescope",
        "settings": {"pbr": True},
    }


def test_3d_image_goes_where_the_model_takes_it():
    one = T.build_3d_task(model3d(image_asset_id=5), "u", {5: "uuid-5"}, HUNYUAN)
    assert one["inputs"] == {"image": "uuid-5"} and "positivePrompt" not in one
    many = T.build_3d_task(model3d(image_asset_id=5), "u", {5: "uuid-5"}, TRIPO)
    assert many["inputs"] == {"images": ["uuid-5"]} and "positivePrompt" not in many
    assert T.build_3d_task(model3d(image_asset_id=5), "u", {5: "uuid-5"}, BARE)["inputs"] == {
        "image": "uuid-5"
    }
    # an asset that did not resolve to a media id is simply not sent
    assert "inputs" not in T.build_3d_task(model3d(image_asset_id=5), "u", {}, HUNYUAN)


def test_build_media_task_dispatches_by_kind():
    assert T.build_media_task("audio", audio(), "u", {}, ACE)["taskType"] == "audioInference"
    assert "speech" in T.build_media_task("speech", speech(), "u", {}, XAI)
    assert (
        T.build_media_task("3d", model3d(prompt="a cup"), "u", {}, HUNYUAN)["taskType"]
        == "3dInference"
    )
    with pytest.raises(ValueError):
        T.build_media_task("image", audio(), "u", {}, ACE)


# ---- request validation --------------------------------------------------------------
def test_requests_refuse_what_cannot_be_generated():
    with pytest.raises(ValidationError, match="describe the music or sound"):
        audio(prompt=" ")
    with pytest.raises(ValidationError, match="type the text to read aloud"):
        speech(text="   ")
    with pytest.raises(ValidationError, match="describe the object or pick an image"):
        model3d()
    with pytest.raises(ValidationError):
        audio(output_format="MP4")
    with pytest.raises(ValidationError):
        model3d(prompt="a cup", output_format="OBJ")
    assert model3d(image_asset_id=3).text() == "3D object from an image"


def test_requests_round_trip_through_the_stored_json():
    for kind, req in (
        ("audio", audio(lyrics="hey")),
        ("speech", speech(voice="eve")),
        ("3d", model3d(prompt="a cup")),
    ):
        data = M.dump(req)
        assert M.request_from_json(kind, data) == req
    assert M.dump(speech())["text"] == "Hello there."


# ---- vocals, lyrics and the model's own rule -----------------------------------------
MM = MINIMAX_MUSIC["constraints"]["fields"] | {"settings.lyricsOptimizer": {"type": "boolean"}}
MM_ROW = {"constraints": {"fields": MM}}


def test_no_lyrics_means_instrumental_on_a_model_that_would_otherwise_demand_them():
    """Seen live: MiniMax Music 2.6 with a prompt and nothing else was rejected with
    "Missing required parameter: '[settings][lyrics]'" -- its rule is "when neither
    instrumental nor lyricsOptimizer is provided, lyrics is required". Lyrics are not
    actually needed for music without vocals, so the request is completed instead of
    sent broken, and it says what it now asks for."""
    req = T.resolve_audio(audio(model="minimax:music@2.6"), MM)
    assert req.instrumental is True and req.lyrics == ""
    assert T.build_audio_task(req, "u", MM_ROW)["settings"] == {"instrumental": True}


def test_vocals_with_your_own_lyrics_are_sent_as_typed():
    req = T.resolve_audio(audio(lyrics="[verse] hold the line"), MM)
    assert req.instrumental is None
    assert T.build_audio_task(req, "u", MM_ROW)["settings"] == {"lyrics": "[verse] hold the line"}
    # "with vocals" said out loud changes nothing on the wire: false is never sent
    explicit = T.resolve_audio(audio(lyrics="[verse] hold the line", instrumental=False), MM)
    assert "instrumental" not in T.build_audio_task(explicit, "u", MM_ROW)["settings"]


def test_vocals_without_lyrics_are_refused_unless_the_model_writes_them():
    with pytest.raises(ValueError, match="Type the lyrics"):
        T.resolve_audio(audio(instrumental=False), MM)
    auto = T.resolve_audio(audio(instrumental=False, settings={"lyricsOptimizer": True}), MM)
    assert T.build_audio_task(auto, "u", MM_ROW)["settings"] == {"lyricsOptimizer": True}
    # the model's writer can also polish lyrics that were typed
    both = T.resolve_audio(
        audio(instrumental=False, lyrics="la la la", settings={"lyricsOptimizer": True}), MM
    )
    assert T.build_audio_task(both, "u", MM_ROW)["settings"] == {
        "lyricsOptimizer": True,
        "lyrics": "la la la",
    }


def test_instrumental_drops_what_the_model_forbids_next_to_it():
    """ "When settings.instrumental is true, settings.lyrics cannot be used" and
    "settings.lyricsOptimizer cannot be true"."""
    req = T.resolve_audio(
        audio(instrumental=True, lyrics="left over", settings={"lyricsOptimizer": True, "x": 1}), MM
    )
    assert req.lyrics == "" and req.settings == {"x": 1}
    assert T.build_audio_task(req, "u", MM_ROW)["settings"] == {"instrumental": True}


def test_a_model_without_the_instrumental_switch_is_left_alone():
    """ACE-Step takes optional lyrics and has no such rule: nothing is added or refused."""
    fields = ACE["constraints"]["fields"]
    assert T.resolve_audio(audio(), fields) == audio()
    assert T.resolve_audio(audio(lyrics="la la la la la"), fields).lyrics == "la la la la la"
    assert T.resolve_audio(audio(), {}) == audio()  # nothing harvested: nothing assumed


# ---- a seed from one model on another with a smaller range ---------------------------------
def test_a_seed_outside_the_model_s_range_is_folded_into_it():
    """Seen live: seed 148681975 (fine on ACE-Step, 0..2147483647) rode along to MiniMax
    Music, whose seeds stop at 1000000: "Invalid value for 'seed'". A seed is only a
    number to start from, so it is mapped into the model's range -- the same seed always
    to the same value, so a repeat still repeats -- instead of failing the job."""
    spec = {"type": "integer", "min": 0, "max": 1000000}
    assert T.fold_seed(148681975, spec) == 148681975 % 1000001
    assert T.fold_seed(148681975, spec) == T.fold_seed(148681975, spec)
    assert T.fold_seed(77, spec) == 77 and T.fold_seed(1000000, spec) == 1000000
    assert T.fold_seed(0, {"min": 1, "max": 20240919}) == 20240919  # Tripo starts at 1
    assert 1 <= T.fold_seed(5_000_000_000, {"min": 1, "max": 20240919}) <= 20240919
    assert T.fold_seed(148681975, {}) == 148681975 and T.fold_seed(148681975, None) == 148681975
    assert T.fold_seed(None, spec) is None
    row = {"constraints": {"fields": MM | {"seed": spec}}}
    task = T.build_audio_task(audio(seed=148681975, instrumental=True), "u", row)
    assert task["seed"] == 148681975 % 1000001
    # resolve() hands back the request as it will be sent, for every kind
    req = T.resolve(audio(seed=148681975, model="minimax:music@2.6"), "audio", MM | {"seed": spec})
    assert req.seed == 148681975 % 1000001 and req.instrumental is True
    same = speech(seed=5)
    assert T.resolve(same, "speech", {"seed": spec}) == same


# ---- several views of one object ---------------------------------------------------------
RODIN = {
    "constraints": {
        "fields": {"positivePrompt": {}},
        "inputs": {"images": {"min_items": 1, "max_items": 5}},
    }
}


def test_a_3d_request_keeps_its_images_in_the_order_they_were_picked():
    """Tripo, Meshy, Rodin and Hunyuan Pro take several views of the same object, and the
    first is the main (front) one: Rodin builds its materials from it."""
    req = model3d(image_asset_ids=[7, 5, 7, 9])
    assert req.image_asset_ids == [7, 5, 9] and req.image_asset_id == 7  # de-duplicated, order kept
    # the single-image field still works on its own, and the two agree
    one = model3d(image_asset_id=5)
    assert one.image_asset_ids == [5]
    assert M.request_from_json("3d", M.dump(req)) == req
    # a request stored before there was a list still loads
    old = {"project_id": 1, "model": "m", "prompt": "", "image_asset_id": 4, "output_format": "GLB"}
    assert M.request_from_json("3d", old).image_asset_ids == [4]
    with pytest.raises(ValidationError, match="a description or an image, not both"):
        model3d(prompt="a cup", image_asset_ids=[1, 2])


def test_every_picked_view_is_sent_to_a_model_that_takes_several():
    media = {7: "u7", 5: "u5", 9: "u9"}
    t = T.build_3d_task(model3d(image_asset_ids=[7, 5, 9]), "u", media, RODIN)
    assert t["inputs"] == {"images": ["u7", "u5", "u9"]}
    # a view whose upload did not resolve is left out, the rest keep their order
    assert T.build_3d_task(model3d(image_asset_ids=[7, 5, 9]), "u", {7: "u7", 9: "u9"}, RODIN)[
        "inputs"
    ] == {"images": ["u7", "u9"]}
    # a single-image model gets the first one only
    assert T.build_3d_task(model3d(image_asset_ids=[7, 5]), "u", media, HUNYUAN)["inputs"] == {
        "image": "u7"
    }
    assert T.max_images(RODIN) == 5 and T.max_images(HUNYUAN) == 1 and T.max_images(TRIPO) == 4
    assert T.max_images(BARE) == 1
