import os
import boto3
from dotenv import load_dotenv

load_dotenv()

bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))

models_to_try = [
    "amazon.titan-text-express-v1",
    "amazon.titan-text-lite-v1",
    "amazon.nova-lite-v1:0",
    "us.amazon.nova-lite-v1:0",
    "amazon.nova-micro-v1:0",
    "us.amazon.nova-micro-v1:0",
    "amazon.nova-pro-v1:0",
    "us.amazon.nova-pro-v1:0",
    "amazon.nova-2-lite-v1:0",
    "us.amazon.nova-2-lite-v1:0",
    "amazon.nova-2-sonic-v1:0",
]

for model_id in models_to_try:
    try:
        response = bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "hello"}]}],
        )
        print(f"WORKS: {model_id}")
    except Exception as e:
        print(f"FAILS: {model_id} -> {str(e)[:80]}")