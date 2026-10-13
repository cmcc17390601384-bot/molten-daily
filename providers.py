"""Endpoint-scoped credentials and dynamic model discovery state."""
from core import protect

PROVIDERS=[('DeepSeek 官方','https://api.deepseek.com'),('硅基流动','https://api.siliconflow.cn/v1'),('自定义兼容接口','')]

def profiles(settings):
    result={k:dict(v) for k,v in settings.get('api_profiles',{}).items()}
    url=settings['base_url'].rstrip('/')
    result.setdefault(url,{})
    result[url].update(api_secret=settings.get('api_secret',''),model=settings.get('model',''))
    return result

def remember(profile_map,url,model,secret=''):
    value=dict(profile_map.get(url,{}));value['model']=model
    if secret:value['api_secret']=protect(secret)
    profile_map[url]=value
    return value
