import json
import parse
import pytz
import requests
import shlex
import subprocess
import time
import yaml

from behave import register_type, step, then
from datetime import datetime, timedelta


@parse.with_pattern(r'https?://(?:\w|\.|:|/)+')
def parse_url(text):
    return text


register_type(url=parse_url)


# there is no way we can find out if the node has already
# started as a leader without checking the DCS. We cannot
# just rely on the database availability, since there is
# a short gap between the time PostgreSQL becomes available
# and Patroni assuming the leader role.
@step('{name:w} is a leader after {time_limit:d} seconds')
@then('{name:w} is a leader after {time_limit:d} seconds')
def is_a_leader(context, name, time_limit):
    max_time = time.time() + int(time_limit)
    while (context.dcs_ctl.query("leader") != name):
        time.sleep(1)
        assert time.time() < max_time, "{0} is not a leader in dcs after {1} seconds".format(name, time_limit)


@step('I sleep for {value:d} seconds')
def sleep_for_n_seconds(context, value):
    time.sleep(int(value))


def _set_response(context, response):
    context.status_code = response.status_code
    data = response.content.decode('utf-8')
    ct = response.headers.get('content-type', '')
    if ct.startswith('application/json') or\
            ct.startswith('text/yaml') or\
            ct.startswith('text/x-yaml') or\
            ct.startswith('application/yaml') or\
            ct.startswith('application/x-yaml'):
        try:
            context.response = yaml.safe_load(data)
        except ValueError:
            context.response = data
    else:
        context.response = data


@step('I issue a GET request to {url:url}')
def do_get(context, url):
    try:
        r = requests.get(url)
    except requests.exceptions.RequestException:
        context.status_code = None
        context.response = None
    else:
        _set_response(context, r)


@step('I issue an empty POST request to {url:url}')
def do_post_empty(context, url):
    do_request(context, 'POST', url, None)


@step('I issue a {request_method:w} request to {url:url} with {data}')
def do_request(context, request_method, url, data):
    data = data and json.loads(data) or {}
    try:
        if request_method == 'PATCH':
            r = requests.patch(url, json=data)
        else:
            r = requests.post(url, json=data)
    except requests.exceptions.RequestException:
        context.status_code = None
        context.response = None
    else:
        _set_response(context, r)


@step('I run {cmd}')
def do_run(context, cmd):
    cmd = ['coverage', 'run', '--source=patroni', '-p'] + shlex.split(cmd)
    try:
        response = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        context.status_code = 0
    except subprocess.CalledProcessError as e:
        response = e.output
        context.status_code = e.returncode
    context.response = response.decode('utf-8').strip()


@then('I receive a response {component:w} {data}')
def check_response(context, component, data):
    if component == 'code':
        assert context.status_code == int(data),\
            "status code {0} != {1}, response: {2}".format(context.status_code, data, context.response)
    elif component == 'returncode':
        assert context.status_code == int(data), "return code {0} != {1}".format(context.status_code, data)
    elif component == 'text':
        assert context.response == data.strip('"'), "response {0} does not contain {1}".format(context.response, data)
    elif component == 'output':
        assert data.strip('"') in context.response, "response {0} does not contain {1}".format(context.response, data)
    else:
        assert component in context.response, "{0} is not part of the response".format(component)
        assert str(context.response[component]) == str(data), "{0} does not contain {1}".format(component, data)


@step('I issue a scheduled failover from {from_host:w} to {to_host:w} in {in_seconds:d} seconds')
def scheduled_failover(context, from_host, to_host, in_seconds):
    context.execute_steps(u"""
        Given I run patronictl.py failover batman --master {0} --candidate {1} --scheduled "{2}" --force
    """.format(from_host, to_host, datetime.now(pytz.utc) + timedelta(seconds=int(in_seconds))))


@step('I issue a scheduled restart at {url:url} in {in_seconds:d} seconds with {data}')
def scheduled_restart(context, url, in_seconds, data):
    data = data and json.loads(data) or {}
    data.update(schedule='{0}'.format((datetime.now(pytz.utc) + timedelta(seconds=int(in_seconds))).isoformat()))
    context.execute_steps(u"""Given I issue a POST request to {0}/restart with {1}""".format(url, json.dumps(data)))


@step('I add tag {tag:w} {value:w} to {pg_name:w} config')
def add_tag_to_config(context, tag, value, pg_name):
    context.pctl.add_tag_to_config(pg_name, tag, value)


@then('Response on GET {url} contains {value} after {timeout:d} seconds')
def check_http_response(context, url, value, timeout, negate=False):
    for _ in range(int(timeout)):
        r = requests.get(url)
        if (value in r.content.decode('utf-8')) != negate:
            break
        time.sleep(1)
    else:
        assert False,\
            "Value {0} is {1} present in response after {2} seconds".format(value, "not" if not negate else "", timeout)


@then('Response on GET {url} does not contain {value} after {timeout:d} seconds')
def check_not_in_http_response(context, url, value, timeout):
    check_http_response(context, url, value, timeout, negate=True)
