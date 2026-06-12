from rest_framework.renderers import JSONRenderer


class PoCJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context.get('response') if renderer_context else None
        if response is not None and response.status_code == 204:
            return super().render(data, accepted_media_type, renderer_context)

        if isinstance(data, dict) and ('success' in data or 'error' in data):
            wrapped = data
        elif response is not None and response.status_code >= 400:
            wrapped = {
                'success': False,
                'error': {
                    'code': 'error',
                    'message': 'Request failed.',
                    'details': data,
                },
            }
        else:
            wrapped = {'success': True, 'data': data}

        return super().render(wrapped, accepted_media_type, renderer_context)
