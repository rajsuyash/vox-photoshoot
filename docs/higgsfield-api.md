# Higgsfield API — endpoint reference

Generated from `https://docs.higgsfield.ai/docs/openapi.json` — spec version 2.0.0.

**Base URL:** `https://platform.higgsfield.ai`
**Auth:** `Authorization: Key <HF_KEY_ID>:<HF_KEY_SECRET>` on every call.

Every generation POST is asynchronous: it returns `{status, request_id, status_url, cancel_url}` immediately. Poll `status_url` or pass `?hf_webhook=<https-url>`.

## Request management

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/requests/{request_id}/status` | Current state + output URLs |
| POST | `/requests/{request_id}/cancel` | Cancel while still `queued` → `202`, empty body |
| POST | `/files/generate-upload-url` | Presigned upload; body `{"content_type": "image/jpeg"}` |
| POST | `/estimate/<model-path>` | Cost preview with the same body → `{"credits", "usd"}` |

Terminal statuses: `completed`, `failed`, `nsfw`, `canceled`. `failed`/`nsfw`/`canceled` are not charged. Output URLs live at least 7 days.

## Available on this account

Verified by calling `/estimate/<path>` on the live account — endpoints absent from the account return `404 model_not_found`, disabled ones `423 model_blocked`. Credits below are the baseline (minimum required params, spec defaults); cost rises with duration, resolution, and batch size.

| Endpoint | Credits |
| --- | ---: |
| `/minimax/hailuo-02/standard/text-to-video` | 1.440 |
| `/higgsfield-ai/popcorn/auto` | 1.472 |
| `/higgsfield-ai/soul/character` | 1.500 |
| `/higgsfield-ai/soul/reference` | 1.500 |
| `/higgsfield-ai/soul/standard` | 1.500 |
| `/higgsfield-ai/dop/lite` | 2.000 |
| `/minimax/hailuo-2.3-fast/standard/image-to-video` | 3.036 |
| `/kling-video/v2.5-turbo/standard/image-to-video` | 3.360 |
| `/minimax/hailuo-02/standard/image-to-video` | 4.476 |
| `/minimax/hailuo-2.3/standard/image-to-video` | 4.476 |
| `/minimax/hailuo-2.3/standard/text-to-video` | 4.476 |
| `/kling-video/v2.1/standard/image-to-video` | 4.480 |
| `/minimax/hailuo-2.3-fast/pro/image-to-video` | 5.280 |
| `/kling-video/v2.5-turbo/pro/image-to-video` | 5.600 |
| `/kling-video/v2.5-turbo/pro/text-to-video` | 5.600 |
| `/higgsfield-ai/dop/turbo` | 6.500 |
| `/minimax/hailuo-02/pro/image-to-video` | 7.800 |
| `/minimax/hailuo-02/pro/text-to-video` | 7.800 |
| `/kling-video/v2.1/pro/image-to-video` | 7.840 |
| `/wan-25-preview/image-to-video` | 8.000 |
| `/wan-25-preview/text-to-video` | 8.000 |
| `/higgsfield-ai/dop/standard` | 9.000 |
| `/kling-video/v2.1/master/image-to-video` | 22.400 |
| `/kling-video/v2.1/master/text-to-video` | 22.400 |

Unavailable: `/bytedance/seedance/v1/lite/image-to-video`, `/bytedance/seedance/v1/lite/text-to-video`, `/bytedance/seedance/v1/pro/fast/image-to-video`, `/bytedance/seedance/v1/pro/fast/text-to-video`, `/flux-pro/kontext/max/text-to-image`, `/minimax/hailuo-2.3/pro/image-to-video`, `/minimax/hailuo-2.3/pro/text-to-video`, `/nano-banana`, `/sora-2/image-to-video`, `/sora-2/image-to-video/pro`, `/sora-2/text-to-video`, `/sora-2/text-to-video/pro`, `/veo3.1`, `/veo3.1/fast`, `/veo3.1/fast/first-last-frame-to-video`, `/veo3.1/fast/image-to-video`, `/veo3.1/first-last-frame-to-video`, `/veo3.1/image-to-video`, `/veo3.1/reference-to-video`.

Blocked: `/reve/edit`, `/reve/fast/edit`, `/reve/fast/remix`, `/reve/remix`, `/reve/text-to-image`.

## Generation endpoints

### Bytedance

#### `POST /bytedance/seedance/v1/lite/image-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | integer |  | min 2, max 12, default `5` |
| `image_url` | string | yes |  |
| `resolution` | `480` \| `720` \| `1080` |  | default `1080` |
| `aspect_ratio` | `16:9` \| `9:16` \| `4:3` \| `3:4` \| `1:1` \| `21:9` |  | default `16:9` |
| `camera_fixed` | boolean |  | default `False` |

#### `POST /bytedance/seedance/v1/lite/text-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | integer |  | min 2, max 12, default `5` |
| `resolution` | `480` \| `720` \| `1080` |  | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` \| `4:3` \| `3:4` \| `1:1` \| `21:9` |  | default `16:9` |
| `camera_fixed` | boolean |  | default `False` |

#### `POST /bytedance/seedance/v1/pro/fast/image-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | integer |  | min 2, max 12, default `5` |
| `image_url` | string | yes |  |
| `resolution` | `480` \| `720` \| `1080` |  | default `1080` |
| `aspect_ratio` | `16:9` \| `9:16` \| `4:3` \| `3:4` \| `1:1` \| `21:9` |  | default `16:9` |
| `camera_fixed` | boolean |  | default `False` |

#### `POST /bytedance/seedance/v1/pro/fast/text-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | integer |  | min 2, max 12, default `5` |
| `resolution` | `480` \| `720` \| `1080` |  | default `1080` |
| `aspect_ratio` | `16:9` \| `9:16` \| `4:3` \| `3:4` \| `1:1` \| `21:9` |  | default `16:9` |
| `camera_fixed` | boolean |  | default `False` |

### Dop

#### `POST /higgsfield-ai/dop/lite`

`available` — baseline **2.000 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000, default `None` |
| `prompt` | string | yes |  |
| `motions` | object{id, strength}[] |  | default `None` |
| `image_url` | string | yes |  |
| `end_image_url` | string |  |  |
| `enhance_prompt` | boolean |  | default `True` |

#### `POST /higgsfield-ai/dop/standard`

`available` — baseline **9.000 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000, default `None` |
| `prompt` | string | yes |  |
| `motions` | object{id, strength}[] |  | default `None` |
| `image_url` | string | yes |  |
| `end_image_url` | string |  |  |
| `enhance_prompt` | boolean |  | default `True` |

#### `POST /higgsfield-ai/dop/turbo`

`available` — baseline **6.500 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000, default `None` |
| `prompt` | string | yes |  |
| `motions` | object{id, strength}[] |  | default `None` |
| `image_url` | string | yes |  |
| `end_image_url` | string |  |  |
| `enhance_prompt` | boolean |  | default `True` |

### Flux Pro

#### `POST /flux-pro/kontext/max/text-to-image`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000 |
| `prompt` | string | yes |  |
| `aspect_ratio` | `16:9` \| `4:3` \| `1:1` \| `3:4` \| `9:16` \| `2:3` \| `1:2` \| `2:1` \| `4:5` \| `3:2` |  | default `16:9` |
| `safety_tolerance` | integer |  | min 0, max 6, default `6` |

### Kling Video

#### `POST /kling-video/v2.1/master/image-to-video`

`available` — baseline **22.400 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `image_url` | string | yes |  |
| `negative_prompt` | string |  | default `` |

#### `POST /kling-video/v2.1/master/text-to-video`

`available` — baseline **22.400 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes | maxLen 2500 |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `aspect_ratio` | `1:1` \| `16:9` \| `9:16` |  | default `1:1` |
| `negative_prompt` | string |  | default `` |

#### `POST /kling-video/v2.1/pro/image-to-video`

`available` — baseline **7.840 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `image_url` | string | yes |  |
| `negative_prompt` | string |  | default `` |

#### `POST /kling-video/v2.1/standard/image-to-video`

`available` — baseline **4.480 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `image_url` | string | yes |  |
| `negative_prompt` | string |  | default `` |

#### `POST /kling-video/v2.5-turbo/pro/image-to-video`

`available` — baseline **5.600 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `image_url` | string | yes |  |
| `negative_prompt` | string |  | default `` |

#### `POST /kling-video/v2.5-turbo/pro/text-to-video`

`available` — baseline **5.600 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `negative_prompt` | string |  | default `` |

#### `POST /kling-video/v2.5-turbo/standard/image-to-video`

`available` — baseline **3.360 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `cfg_scale` | number |  | min 0, max 1, default `0.5` |
| `image_url` | string | yes |  |
| `negative_prompt` | string |  | default `` |

### Minimax

#### `POST /minimax/hailuo-02/pro/image-to-video`

`available` — baseline **7.800 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_url` | string | yes |  |
| `end_image_url` | string |  |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-02/pro/text-to-video`

`available` — baseline **7.800 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-02/standard/image-to-video`

`available` — baseline **4.476 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `6` \| `10` |  | default `6` |
| `image_url` | string | yes |  |
| `resolution` | `512P` \| `768P` |  | default `768P` |
| `end_image_url` | string |  |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-02/standard/text-to-video`

`available` — baseline **1.440 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `6` \| `10` |  | default `6` |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-2.3-fast/pro/image-to-video`

`available` — baseline **5.280 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_url` | string | yes |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-2.3-fast/standard/image-to-video`

`available` — baseline **3.036 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `6` \| `10` |  | default `6` |
| `image_url` | string | yes |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-2.3/pro/image-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_url` | string | yes |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-2.3/pro/text-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-2.3/standard/image-to-video`

`available` — baseline **4.476 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `6` \| `10` |  | default `6` |
| `image_url` | string | yes |  |
| `prompt_optimizer` | boolean |  | default `True` |

#### `POST /minimax/hailuo-2.3/standard/text-to-video`

`available` — baseline **4.476 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `6` \| `10` |  | default `6` |
| `prompt_optimizer` | boolean |  | default `True` |

### Nano Banana

#### `POST /nano-banana`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |
| `aspect_ratio` | `auto` \| `1:1` \| `4:3` \| `3:4` \| `3:2` \| `2:3` \| `5:4` \| `4:5` \| `16:9` \| `9:16` \| `21:9` |  | default `4:3` |
| `input_images` | object{type, image_url}[] |  |  |
| `output_format` | `jpeg` \| `png` |  | default `jpeg` |

### Popcorn

#### `POST /higgsfield-ai/popcorn/auto`

`available` — baseline **1.472 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000, default `None` |
| `prompt` | string | yes |  |
| `image_urls` | string[] |  |  |
| `num_images` | integer |  | min 1, max 8, default `1` |
| `resolution` | `720p` \| `1600p` |  | default `720p` |
| `aspect_ratio` | `1:1` \| `4:3` \| `3:4` \| `3:2` \| `2:3` \| `16:9` \| `9:16` |  | default `4:3` |

### Reve

#### `POST /reve/edit`

`blocked`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_url` | string | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |

#### `POST /reve/fast/edit`

`blocked`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_url` | string | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |

#### `POST /reve/fast/remix`

`blocked`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_urls` | string[] | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |
| `aspect_ratio` | `1:1` \| `4:3` \| `3:4` \| `3:2` \| `2:3` \| `5:4` \| `4:5` \| `16:9` \| `9:16` |  | default `4:3` |

#### `POST /reve/remix`

`blocked`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `image_urls` | string[] | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |
| `aspect_ratio` | `1:1` \| `4:3` \| `3:4` \| `3:2` \| `2:3` \| `5:4` \| `4:5` \| `16:9` \| `9:16` |  | default `4:3` |

#### `POST /reve/text-to-image`

`blocked`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |

### Sora 2

#### `POST /sora-2/image-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `8` \| `12` |  | default `4` |
| `image_url` | string |  |  |
| `resolution` | `720p` |  | default `720p` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |

#### `POST /sora-2/image-to-video/pro`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `8` \| `12` |  | default `4` |
| `image_url` | string | yes |  |
| `resolution` | `720p` \| `1080p` |  | default `720p` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |

#### `POST /sora-2/text-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `8` \| `12` |  | default `4` |
| `resolution` | `720p` |  | default `720p` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |

#### `POST /sora-2/text-to-video/pro`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `8` \| `12` |  | default `4` |
| `resolution` | `720p` \| `1080p` |  | default `720p` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |

### Soul

#### `POST /higgsfield-ai/soul/character`

`available` — baseline **1.500 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000, default `None` |
| `prompt` | string | yes |  |
| `style_id` | string |  | default `None` |
| `batch_size` | `1` \| `4` |  | default `1` |
| `resolution` | `720p` \| `1080p` |  | default `720p` |
| `aspect_ratio` | `9:16` \| `16:9` \| `4:3` \| `3:4` \| `1:1` \| `2:3` \| `3:2` |  | default `4:3` |
| `enhance_prompt` | boolean |  | default `True` |
| `style_strength` | number |  | min 0, max 1, default `1` |
| `custom_reference_id` | string | yes |  |
| `image_reference_url` | string |  |  |
| `custom_reference_strength` | number | yes | min 0, max 1, default `1` |

#### `POST /higgsfield-ai/soul/reference`

`available` — baseline **1.500 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min 1, max 1000000, default `None` |
| `prompt` | string | yes |  |
| `style_id` | string |  | default `None` |
| `batch_size` | `1` \| `4` |  | default `1` |
| `resolution` | `720p` \| `1080p` |  | default `720p` |
| `aspect_ratio` | `9:16` \| `16:9` \| `4:3` \| `3:4` \| `1:1` \| `2:3` \| `3:2` |  | default `4:3` |
| `enhance_prompt` | boolean |  | default `True` |
| `style_strength` | number |  | min 0, max 1, default `1` |
| `image_reference_url` | string | yes |  |

#### `POST /higgsfield-ai/soul/standard`

`available` — baseline **1.500 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `num_images` | integer |  | min 1, max 4, default `1` |
| `resolution` | `720p` \| `1080p` |  | **spec is wrong** — live values shown, spec claimed `2K`, `4K` |
| `aspect_ratio` | `9:16` \| `16:9` \| `4:3` \| `3:4` \| `1:1` \| `2:3` \| `3:2` |  | **spec is wrong** — live values shown, spec claimed `1:1`, `4:3`, `3:4`, `3:2`, `2:3`, `5:4`, `4:5`, `16:9`, `9:16`, `21:9` |

### Veo3.1

#### `POST /veo3.1`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `resolution` | `720` \| `1080` | yes | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` | yes | default `16:9` |
| `generate_audio` | boolean | yes | default `False` |

#### `POST /veo3.1/fast`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `resolution` | `720` \| `1080` | yes | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` | yes | default `16:9` |
| `generate_audio` | boolean | yes | default `False` |

#### `POST /veo3.1/fast/first-last-frame-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `resolution` | `720` \| `1080` |  | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |
| `generate_audio` | boolean |  | default `False` |
| `last_frame_url` | string | yes | minLen 3 |
| `first_frame_url` | string | yes | minLen 3 |

#### `POST /veo3.1/fast/image-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `image_url` | string | yes | minLen 3 |
| `resolution` | `720` \| `1080` |  | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |
| `generate_audio` | boolean |  | default `False` |

#### `POST /veo3.1/first-last-frame-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `resolution` | `720` \| `1080` |  | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |
| `generate_audio` | boolean |  | default `False` |
| `last_frame_url` | string | yes | minLen 3 |
| `first_frame_url` | string | yes | minLen 3 |

#### `POST /veo3.1/image-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `image_url` | string | yes | minLen 3 |
| `resolution` | `720` \| `1080` |  | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |
| `generate_audio` | boolean |  | default `False` |

#### `POST /veo3.1/reference-to-video`

`not on this account`

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `prompt` | string | yes |  |
| `duration` | `4` \| `6` \| `8` |  | default `6` |
| `image_urls` | string[] | yes |  |
| `resolution` | `720` \| `1080` |  | default `720` |
| `aspect_ratio` | `16:9` \| `9:16` |  | default `16:9` |
| `generate_audio` | boolean |  | default `False` |

### Wan 25 Preview

#### `POST /wan-25-preview/image-to-video`

`available` — baseline **8.000 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min -1, max 1000000, default `-1` |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `audio_url` | string |  |  |
| `image_url` | string | yes |  |
| `resolution` | `480p` \| `720p` \| `1080p` |  | default `720p` |
| `negative_prompt` | string |  | default `` |

#### `POST /wan-25-preview/text-to-video`

`available` — baseline **8.000 credits**

| Param | Type | Req | Notes |
| --- | --- | :-: | --- |
| `seed` | integer |  | min -1, max 1000000, default `-1` |
| `prompt` | string | yes |  |
| `duration` | `5` \| `10` |  | default `5` |
| `audio_url` | string |  |  |
| `resolution` | `480p` \| `720p` \| `1080p` |  | default `720p` |
| `negative_prompt` | string |  | default `` |

