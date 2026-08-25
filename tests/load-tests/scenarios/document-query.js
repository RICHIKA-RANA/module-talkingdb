import http from 'k6/http';
import { check, sleep } from 'k6';

export function runDocumentQuery(baseUrl, token, graph_id) {
    const url = `${baseUrl}/v1/queries`;

    const payload = JSON.stringify({
        graph_ids: [
            graph_id
        ],
        text: "microsoft",
        max_results: 5,
        summarize: false
    });

    const params = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        }
    };

    const res = http.post(url, payload, params);

    check(res, {
        'query status is 200': (r) => r.status === 200
    });

    sleep(Math.floor(Math.random() * 2) + 1);
}