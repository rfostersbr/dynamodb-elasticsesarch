from __future__ import print_function

import json
import base64
import re
import os
import logging
from elasticsearch import Elasticsearch


# Process DynamoDB Stream records and insert the object in ElasticSearch
# Use the Table name as index and doc_type name
# Force index refresh upon all actions for close to realtime reindexing
# Use IAM Role for authentication
# Properly unmarshal DynamoDB JSON types. Binary NOT tested.

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)
logger.info("Logging Configured...")

def decode_record_data(record):
    dec = ''
    base64.decode(record['data'], dec)

    return dec


def process_stream(event, context):
    # Connect to ES
    es = Elasticsearch(
        [os.environ['ES_ENDPOINT']],
    )

    logger.info("Cluster info:")
    logger.info(es.info())

    # Loop over the DynamoDB Stream records
    for record in event['Records']:

        logger.info("New Record to process:")
        logger.info(json.dumps(record))

        try:

            if record['eventName'] == "INSERT" or record['eventName'] == "aws:kinesis:record":
                insert_document(es, record)
            elif record['eventName'] == "REMOVE": #or record['eventName'] == "aws:kinesis:record"#:
                remove_document(es, record)
            elif record['eventName'] == "MODIFY": #or record['eventName'] == "aws:kinesis:record"#:
                modify_document(es, record)

        except Exception as e:
            logger.info(e)
            continue


# Process MODIFY events
def modify_document(es, record):
    table = get_table(record)
    logger.info("Dynamo Table: " + table)

    doc_id = generate_id(record)
    logger.info("KEY")
    logger.info(doc_id)

    # Unmarshal the DynamoDB JSON to a normal JSON
    doc = json.dumps(unmarshal_json(record['dynamodb']['NewImage']))

    logger.info("Updated document:")
    logger.info(doc)

    # We reindex the whole document as ES accepts partial docs
    es.index(index=table,
             body=doc,
             id=doc_id,
             doc_type=table,
             refresh=True)

    logger.info("Success - Updated index ID: " + doc_id)


# Process REMOVE events
def remove_document(es, record):
    table = get_table(record)
    logger.info("Dynamo Table: " + table)

    doc_id = generate_id(record)
    logger.info("Deleting document ID: " + doc_id)

    es.delete(index=table,
              id=doc_id,
              doc_type=table,
              refresh=True)

    logger.info("Successly removed")


# Process INSERT events
def insert_document(es, record):
    table = get_table(record)
    logger.info("Dynamo Table: " + table)

    # Create index if missing
    if not es.indices.exists(table):
        logger.info("Create missing index: " + table)

        es.indices.create(table,
                          body='{"settings": { "index.mapping.coerce": true } }')

        logger.info("Index created: " + table)

    # Unmarshal the DynamoDB JSON to a normal JSON
    doc = ''
    if record['eventName'] == "aws:kinesis:record":
        record_data = decode_record_data(record)
        doc = json.dumps(unmarshal_json(record_data['dynamodb']['NewImage']))
    else:
        doc = json.dumps(unmarshal_json(record['dynamodb']['NewImage']))

    logger.info("New document to Index:")
    logger.info(doc)

    new_id = generate_id(record)
    es.index(index=table,
             body=doc,
             id=new_id,
             doc_type=table,
             refresh=True)

    logger.info("Success - New Index ID: " + new_id)


# Return the dynamoDB table that received the event. Lower case it
def get_table(record):
    p = re.compile('arn:aws:(?:dynamodb|kinesis):.*?:.*?:table/([0-9a-zA-Z_-]+)/.+')
    m = p.match(record['eventSourceARN'])
    if m is None:
        raise Exception("Table not found in SourceARN")
    return m.group(1).lower()


# Generate the ID for ES. Used for deleting or updating item later
def generate_id(record):
    keys = unmarshal_json(record['dynamodb']['Keys'])

    # Concat HASH and RANGE key with | in between
    new_id = ""
    i = 0
    for key, value in keys.items():
        if i > 0:
            new_id += "|"
        new_id += str(value)
        i += 1

    return new_id


# Unmarshal a JSON that is DynamoDB formatted
def unmarshal_json(node):
    data = {"M": node}
    return unmarshal_value(data, True)


# ForceNum will force float or Integer to
def unmarshal_value(node, force_num=False):
    for key, value in node.items():
        if key == "NULL":
            return None
        if key == "S" or key == "BOOL":
            return value
        if key == "N":
            if force_num:
                return int_or_float(value)
            return value
        if key == "M":
            data = {}
            for key1, value1 in value.items():
                data[key1] = unmarshal_value(value1, True)
            return data
        if key == "BS" or key == "L":
            data = []
            for item in value:
                data.append(unmarshal_value(item))
            return data
        if key == "SS":
            data = []
            for item in value:
                data.append(item)
            return data
        if key == "NS":
            data = []
            for item in value:
                if force_num:
                    data.append(int_or_float(item))
                else:
                    data.append(item)
            return data


# Detect number type and return the correct one
def int_or_float(s):
    try:
        return int(s)
    except ValueError:
        return float(s)
