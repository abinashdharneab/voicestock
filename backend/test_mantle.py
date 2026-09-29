from dotenv import load_dotenv
load_dotenv()

from anthropic import AnthropicBedrockMantle

client = AnthropicBedrockMantle(
    aws_region="us-east-1",
    default_headers={"anthropic-workspace-id": "default"},
)

message = client.messages.create(
    model="anthropic.claude-haiku-4-5",
    max_tokens=64,
    messages=[{"role": "user", "content": "What is Amazon Bedrock?"}],
)
print(message.content[0].text)