"""OpenRouter-only transport. Never include provider bodies or credentials in errors."""
import time
import httpx

BASE_URL = 'https://openrouter.ai/api/v1'
MODEL_TTL = 300
_cache = {'expires': 0, 'models': []}

class ProviderError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status

ERRORS = {
    400: 'OpenRouter rejected the request. Check the model ID and choose a model with tool support.',
    401: 'OpenRouter rejected the API key. Enter a valid OpenRouter API key and save again.',
    402: 'OpenRouter reports insufficient credits. Add credits or choose an available free model.',
    403: 'OpenRouter denied access. Check the key permissions and your OpenRouter privacy settings.',
    404: 'This OpenRouter model or tool-capable provider is unavailable. Choose another model.',
    408: 'OpenRouter timed out. Try again or choose another model.',
    429: 'OpenRouter rate limit reached. Wait and retry, or choose another model.',
    502: 'The model provider behind OpenRouter failed. Try again or choose another model.',
    503: 'No OpenRouter provider is currently available for this request. Try another model.',
}

async def request(method, path, key=None, payload=None):
    headers = {'X-Title': 'Urumi Gateway'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
            response = await client.request(method, BASE_URL + path, headers=headers, json=payload)
        try:
            data = response.json()
        except ValueError:
            data = None
        error = data.get('error') if isinstance(data, dict) else None
        if response.is_error or error:
            status = response.status_code
            if status < 400 and isinstance(error, dict):
                try: status = int(error.get('code', 502))
                except (ValueError, TypeError): status = 502
            raise ProviderError(ERRORS.get(status, 'OpenRouter could not complete the request. Try again or choose another model.'), status if status in ERRORS else 502)
        if not isinstance(data, dict):
            raise ProviderError('OpenRouter returned an invalid response. Please retry.')
        return data
    except ProviderError:
        raise
    except httpx.TimeoutException:
        raise ProviderError(ERRORS[408], 504) from None
    except httpx.RequestError:
        raise ProviderError('Cannot connect to OpenRouter. Check the server internet connection and retry.', 503) from None

async def models():
    if time.monotonic() < _cache['expires']:
        return _cache['models']
    data = await request('GET', '/models')
    if not isinstance(data.get('data'), list):
        raise ProviderError('OpenRouter returned an invalid model catalog. Please retry.')
    result = []
    for model in data['data']:
        supported = model.get('supported_parameters') or []
        if 'tools' not in supported or 'tool_choice' not in supported:
            continue
        if not isinstance(model.get('id'), str):
            continue
        pricing = model.get('pricing') or {}
        free = str(pricing.get('prompt')) == '0' and str(pricing.get('completion')) == '0'
        result.append({'id': model['id'], 'name': model.get('name', model['id']), 'free': free})
    result.sort(key=lambda item: (not item['free'], item['name'].lower()))
    _cache.update(models=result, expires=time.monotonic() + MODEL_TTL)
    return result

async def validate_key(key):
    await request('GET', '/key', key)

async def complete(model, key, messages, tools=None, tool_choice='auto', max_tokens=4096):
    payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens}
    if tools:
        payload.update(tools=tools, tool_choice=tool_choice, provider={'require_parameters': True})
    data = await request('POST', '/chat/completions', key, payload)
    choices = data.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError('OpenRouter returned no model response. Try again or select another model.')
    message = choices[0].get('message')
    if not isinstance(message, dict) or not (message.get('content') or message.get('tool_calls')):
        raise ProviderError('The model returned an empty response. Try again or select another model.')
    return message
