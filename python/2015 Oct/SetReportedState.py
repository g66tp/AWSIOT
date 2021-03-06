#!/usr/bin/env python

# This program tells AWS IoT the reported state of our device. It sends a REST request to AWS IoT over HTTPS.
# The program here uses digital signatures to sign the REST request so that the AWS IoT server can authenticate us.
# This program expects 3 parameters from the AWS Lambda event:
#   device: Name of device, e.g. g88_pi
#   attribute: Name of sensor/actuator, e.g. led
#   value: Reported state of sensor/actuator, e.g. on

import sys, os, datetime, hashlib, hmac, urllib2, json, base64, pickle

# List of device names and the replacement Slack channels for the device.
# Used if the channel name is already taken up.  Sync this with ActuateDeviceFromSlack and
# SendSensorDataToElasticsearch.
replaceSlackChannels = {
    "g88": "g88a",
    "g29": "g29a"
}

# TODO: Name of our Raspberry Pi, also known as our "Thing Name".  Used only when running from command-line.
deviceName = "g88_pi"


def lambda_handler(event, context):
    # This is the main logic of the program. We construct a JSON payload to tell AWS IoT to set our device's desired
    # state. Then we wrap the JSON payload as a REST request and send to AWS IoT over HTTPS. The REST request needs
    # to be signed so that the AWS IoT server can authenticate us. This code is written as an AWS Lambda handler so
    # that we can run this code on the command line as well as AWS Lambda.
    print("AWS Lambda event: " + json.dumps(event, indent=4))

    try:
        # We want to handle 3 types of input: Kinesis, IoT Rule and REST
        if event.get('Records') is not None:
            # If Kinesis, get the batch of records. Kinesis supports multiple records.
            records = event.get('Records')
        else:
            # If IoT Rule or REST, we should expect only 1 input record.
            records = [event]

        # We loop and process every record received.
        for record in records:

            # Kinesis data is encoded with Base-64 so we need to decode.
            if record.get('kinesis') is not None:
                record = json.loads(base64.b64decode(record['kinesis']['data']))
                print("Decoded payload from Kinesis: " + json.dumps(record, indent=2))

            # Get the device, attribute and value parameters from the caller (e.g. IoT Rule).
            device = record.get("device")
            attribute = record.get("attribute")
            value = record.get("value")

            # If the parameters were not provided, we stop.
            if device is None:
                raise RuntimeError("Missing parameter for device")
            if attribute is None:
                raise RuntimeError("Missing parameter for attribute")
            if value is None:
                raise RuntimeError("Missing parameter for value")

            # Construct the JSON payload to set the reported state for our sensor/actuator.
            payload = {
                "state": {
                    "reported": {
                        attribute: value,
                        "timestamp": datetime.datetime.now().isoformat()
                    }
                }
            }
            print("REST request payload: " + json.dumps(payload, indent=4))
            post_to_slack(device, "Sending reported state to device:\n```" + json.dumps(payload, indent=4) + "```")

            # Send the "set reported state" request to AWS IoT via a REST request over HTTPS.  We are actually updating
            # the Thing Shadow, according to AWS IoT terms.
            result = send_aws_iot_request("POST", device, json.dumps(payload))
            print("Result of REST request:\n" +
                  json.dumps(result, indent=4, separators=(',', ': ')))
            if json.dumps(result).find("metadata") > 0:
                slackResult = "Device has set reported state successfully"
            else:
                slackResult = "Error: Device failed to set reported state"
            post_to_slack(device, slackResult)

    except:
        # In case of error, show the exception.
        print('REST request failed')
        raise
    else:
        # If no error, return the result.
        return result
    finally:
        # If any case, display "completed".
        print('REST request completed')


def send_aws_iot_request(method, device_name2, payload2):
    # Send a REST request to AWS IoT over HTTPS.  Only method "POST" is supported, which will update the Thing Shadow
    # for the specified device with the specified payload.
    # This is the access key for user lambda_iot_user.  Somehow we can't sign using the AWS Lambda access key.
    access_key = 'AKIAIAAXOWVF3FX2XBZA'
    secret_key = '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'
    if access_key is None or secret_key is None:
        print('No access key is available.')
        sys.exit()

    # Create a date for headers and the credential string
    t = datetime.datetime.utcnow()
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')  # Date w/o time, used in credential scope

    # ************* TASK 1: CREATE A CANONICAL REQUEST *************
    # http://docs.aws.amazon.com/general/latest/gr/sigv4-create-canonical-request.html

    # Step 1 is to define the verb (GET, POST, etc.)--already done.

    # Step 2: Create canonical URI--the part of the URI from domain to query string (use '/' if no path)
    canonical_uri = '/things/' + device_name2 + '/shadow'

    # Step 3: Create the canonical query string.
    canonical_querystring = ''

    # Step 4: Create the canonical headers. Header names and values must be trimmed and lowercase, and sorted in ASCII
    # order. Note that there is a trailing \n.  Use AWS command line "aws iot describe-endpoint" to get the address.
    host = 'A1P01IYM2DOZA0.iot.us-west-2.amazonaws.com'
    user_agent = "TP-IoT"  # Any value should work.
    canonical_headers = 'host:' + host + '\n' + \
                        'user-agent:' + user_agent + '\n' + \
                        'x-amz-date:' + amz_date + '\n'
    print("REST request header values to be signed (canonical_headers):\n<<\n" + canonical_headers + ">>\n")

    # Step 5: Create the list of signed headers. This lists the headers in the canonical_headers list, delimited with
    # ";" and in alpha order. Note: The request can include any headers; canonical_headers and signed_headers include
    # those that you want to be included in the hash of the request. "Host" and "x-amz-date" are always required. For
    # IoT, user-agent is also required.
    signed_headers = 'host;user-agent;x-amz-date'
    print("REST request header fields to be signed (signed_headers): " + signed_headers)

    # Step 6: Create payload hash. In this example, the payload (body of the request) contains the request parameters.
    payload_hash = hashlib.sha256(payload2).hexdigest()
    print("REST payload hash: " + payload_hash)

    # Step 7: Combine elements to create create canonical request
    canonical_request = method + '\n' + canonical_uri + '\n' + canonical_querystring + '\n' + \
                        canonical_headers + '\n' + signed_headers + '\n' + payload_hash
    print("REST request to be signed (canonical_request):\n<<\n" + canonical_request + "\n>>\n")

    # ************* TASK 2: CREATE THE STRING TO SIGN*************
    # Match the algorithm to the hashing algorithm you use, either SHA-1 or SHA-256 (recommended)
    region = 'us-west-2'
    service = 'iotdata'
    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = date_stamp + '/' + region + '/' + service + '/' + 'aws4_request'
    print("REST credential scope: " + credential_scope)
    string_to_sign = algorithm + '\n' + amz_date + '\n' + credential_scope + '\n' + \
                     hashlib.sha256(canonical_request).hexdigest()
    print("REST request hash to be signed (string_to_sign):\n<<\n" + string_to_sign + "\n>>\n")

    # ************* TASK 3: CALCULATE THE SIGNATURE *************
    # Create the signing key using the function defined above.
    signing_key = get_signature_key(secret_key, date_stamp, region, service)
    # Sign the string_to_sign using the signing_key
    signature = hmac.new(signing_key, (string_to_sign).encode('utf-8'), hashlib.sha256).hexdigest()

    # ************* TASK 4: ADD SIGNING INFORMATION TO THE REQUEST *************
    # Put the signature information in a header named Authorization.
    authorization_header = algorithm + ' ' + 'Credential=' + access_key + '/' + credential_scope + ', ' + \
                           'SignedHeaders=' + signed_headers + ', ' + 'Signature=' + signature
    print("REST request authorization header:\n<<\n" + authorization_header.replace(" ", "\n") + "\n>>\n")

    # For AWS IoT, the request should include the following. The headers must be included in the canonical_headers and
    # signed_headers values, as noted earlier. Order here is not significant.
    content_type = ""
    headers = {'Content-Type': content_type,
               'Host': host,
               'User-Agent': user_agent,
               'X-Amz-Date': amz_date,
               'Authorization': authorization_header}
    print("REST request header values:\n<<\n" + str(headers).replace(",", ",\n") + "\n>>\n")

    # ************* SEND THE REQUEST *************
    url = "https://" + host + canonical_uri
    print("Sending REST request via HTTPS " + method + " to URL " + url + "...")
    request = urllib2.Request(url, payload2, headers)
    result2 = urllib2.urlopen(request).read()
    # Parse the result as JSON and return as a dictionary.
    return json.loads(result2)


def sign(key, msg):
    # Function for signing a HTTPS request to AWS, so that AWS can authenticate us.  See:
    # http://docs.aws.amazon.com/general/latest/gr/signature-v4-examples.html#signature-v4-examples-python
    # Return the signature of the message, signed with the specified key.
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def get_signature_key(key, date_stamp, region_name, service_name):
    # Also used for signing the HTTPS request to AWS.
    # Return the key to be used for signing the request.
    kdate = sign(('AWS4' + key).encode('utf-8'), date_stamp)
    kregion = sign(kdate, region_name)
    kservice = sign(kregion, service_name)
    ksigning = sign(kservice, 'aws4_request')
    return ksigning


def post_to_slack(device, action):
    # Post a Slack message to the channel of the same name as the device e.g. #g88.
    # device is assumed to begin with the group name.  action is the message.
    if device is None:
        return
    channel = "g88a"
    # If device is g88_pi, then post to channel #g88.
    pos = device.find("_")
    if pos > 0:
        channel = device[0:pos]
    # Map the channel name in case the channel name is unavailable.
    if replaceSlackChannels.get(channel) is not None:
        channel = replaceSlackChannels.get(channel)
    # Construct the REST request to Slack.
    body = {
        "channel": "#" + channel,  # Public channels always start with #
        "username": device,
        "text": action
    }
    print(json.dumps(body, indent=2))
    url = "https://hooks.slack.com/services/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"
    try:
        # Make the REST request to Slack.
        request = urllib2.Request(url, json.dumps(body))
        result2 = urllib2.urlopen(request).read()
        print("result = " + result2)
        return result2
    except error:
        # Show the error.
        error_content = error.read()
        print("error = " + error_content)


# The main program starts here.  If started from a command line, run the lambda function manually.
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") is None:
    # If running on command line, we set the LED attribute of the device.
    event0 = {
        "device": deviceName,
        "attribute": "led",
        "value": "on"
    }
    # Start the lambda function.
    lambda_handler(event0, {})

'''
Some of the above signature settings were obtained from capturing the debug output of the AWS command line tool:
aws --debug --region us-west-2 --profile tp-iot iot-data update-thing-shadow --thing-name g0_temperature_sensor --payload "{ \"state\": {\"desired\": { \"led\": \"on\" } } }"  output.txt && cat output.txt
aws --debug --endpoint-url http://g89-pi.local --no-verify-ssl --region us-west-2 --profile tp-iot iot-data update-thing-shadow --thing-name g0_temperature_sensor --payload "{ \"state\": {\"desired\": { \"led\": \"on\" } } }"  output.txt && cat output.txt

lambda_iot_user has the following policy:
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "iot:UpdateThingShadow"
            ],
            "Resource": [
                "*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "kinesis:GetRecords",
                "kinesis:GetShardIterator",
                "kinesis:DescribeStream",
                "kinesis:ListStreams"
            ],
            "Resource": [
                "*"
            ]
        }
    ]
}
'''