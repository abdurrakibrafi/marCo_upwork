from django.shortcuts import render
from django.http import JsonResponse
from datetime import datetime
from django.http import HttpResponse
import json

from apps.core.utils.decorators import basic_auth_required
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

@basic_auth_required
def api_root(request):
    """Enhanced API root endpoint with comprehensive information"""
    # Build absolute URLs
    base_url = request.build_absolute_uri('/')[:-1]
    
    response_data = {
        '🏠 welcome': {
            'message': 'Welcome to MySportsNest API',
            'version': 'v1.0.0',
            'developer': 'Built with 🐍 by Rafi',
            'status': '✅ Healthy & Running',
            'timestamp': datetime.now().isoformat(),
        },
        
        '📚 documentation': {
            'swagger': {
                'url': f"{base_url}/api/docs/swagger/",
                'description': '🎨 Interactive API documentation with Swagger UI',
                'recommended': '⭐ Best for testing endpoints'
            },
            'redoc': {
                'url': f"{base_url}/api/docs/redoc/",
                'description': '📖 Clean, readable API documentation',
                'recommended': '⭐ Best for reading & understanding'
            },
            'schema': {
                'download_from': f"{base_url}/api/docs/swagger/",
                'description': '🔧 Raw OpenAPI 3.0 schema (JSON/YAML)',
                'note': '💡 Tip: Download from Swagger UI (link above) - click /api/schema link at the top'
            },
        },
        
        '📊 api_info': {
            'base_url': base_url,
            'format': 'JSON',
            'authentication': 'Token-based (check docs for details)',
            'rate_limiting': 'Configured (check headers for limits)',
            'cors': 'Enabled for allowed origins'
        },
        
        '💡 getting_started': {
            '1️⃣ explore': 'Visit Swagger UI to see all available endpoints',
            '2️⃣ authenticate': 'Get your API token from /api/auth/login/',
            '3️⃣ test': 'Use the "Try it out" feature in Swagger',
            '4️⃣ integrate': 'Download the schema for your frontend framework'
        },

        '📮 postman_collection': {
            'invitation_link': 'https://app.postman.com/join-team?invite_code=YOUR_INVITE_CODE_HERE',
            'how_to': '👉 Click the link, join the team, start testing. That\'s it!',
        },

        '💝 Love Letter to Frontend Devs': {
            'to': '👋 Hey Syful Bro!',
            'from': 'Your Backend (aka Rafi)',
            'message': 'May your console be error-free and your builds be fast! ⚡',
            'features': [
                '✅ Clear, consistent endpoint naming',
                '✅ Detailed error messages',
                '✅ Request/response examples everywhere',
                '✅ Proper HTTP status codes',
                '✅ Interactive documentation',
                '✅ No surprises, just good APIs'
            ],
            'collab_status': '🤝 Crushing it together!', 
            'ps': '😄 Yes, we know backend is harder than frontend (just kidding... or are we?)',
            'pps': 'Bhai, thanks for making my JSON look good on screen!'
        },
        
        '📞 Need Help?': {
            'documentation': '📚 Check the docs first (seriously, read them!)',
            'backend_team': '💬 Reach out to Rafi',
            'pro_tip': '🎯 90% of questions are answered in Swagger docs',
            'last_resort': '🆘 If nothing works, we\'ll debug together',
            'postman_help': '📮 Stuck with Postman? Just ping Rafi!'
        },
    }
    
    # Convert to JSON string
    json_output = json.dumps(response_data, indent=2, ensure_ascii=False)
    
    # HTML with syntax highlighting CSS
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MySportsNest API</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            font-family: 'Courier New', Courier, monospace;
            background: #f5f5f5;
            font-size: 14px;
            line-height: 1.6;
        }}
        pre {{
            margin: 20px;
            padding: 20px;
            background: white;
            border-radius: 5px;
            border: 1px solid #ddd;
            overflow-x: auto;
        }}
        
        /* JSON Syntax Highlighting */
        .json-key {{ color: #0066cc; font-weight: bold; }}
        .json-string {{ color: #669900; }}
        .json-number {{ color: #ff6600; }}
        .json-boolean {{ color: #cc0000; }}
        .json-null {{ color: #cc0000; }}
        
        /* Make URLs clickable */
        .json-url {{
            color: #0066cc;
            text-decoration: underline;
            cursor: pointer;
        }}
        .json-url:hover {{
            color: #0044aa;
        }}
    </style>
</head>
<body>
<pre id="json">{json_output}</pre>

<script>
// Simple JSON syntax highlighting with clickable URLs
function syntaxHighlight(json) {{
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{{4}}|\\[^u]|[^\\"])*"(\\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {{
        var cls = 'json-number';
        if (/^"/.test(match)) {{
            if (/:$/.test(match)) {{
                cls = 'json-key';
            }} else {{
                cls = 'json-string';
                // Check if it's a URL
                var urlMatch = match.match(/"(https?:\/\/[^"]+)"/);
                if (urlMatch) {{
                    var url = urlMatch[1];
                    return '"<a href="' + url + '" class="json-url" target="_blank">' + url + '</a>"';
                }}
            }}
        }} else if (/true|false/.test(match)) {{
            cls = 'json-boolean';
        }} else if (/null/.test(match)) {{
            cls = 'json-null';
        }}
        return '<span class="' + cls + '">' + match + '</span>';
    }});
}}

document.getElementById('json').innerHTML = syntaxHighlight(document.getElementById('json').textContent);
</script>
</body>
</html>"""
    
    response = HttpResponse(html_content, content_type='text/html; charset=utf-8')
    
    # Add custom headers
    response['X-API-Developer'] = 'Rafi 👨‍💻'
    response['X-API-Message'] = 'Happy Coding!'
    response['X-Made-With'] = '🐍 and ☕'
    response['X-Frontend-Hero'] = 'Syful 🎨'
    
    return response


