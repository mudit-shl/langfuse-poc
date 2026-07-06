AUTH=$(echo -n "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64 -w 0)
echo $AUTH
for f in traces/interview/*.json; do
	echo -n "$(basename $f): "
	curl -s -o /dev/null -w "%{http_code}\n" \
		-X POST http://localhost:3000/api/public/otel/v1/traces \
		-H "Content-Type: application/json" \
		-H "Authorization: Basic $AUTH" \
		-d @"$f"
done