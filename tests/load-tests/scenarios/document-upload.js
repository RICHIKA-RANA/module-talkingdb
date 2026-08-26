import http from 'k6/http';
import { check, sleep } from 'k6';

const pdfFile = open('../assets/Ask-Microsoft-transparency-FAQ.pdf', 'b');

export function runDocumentUpload(baseUrl, token) {
    const url = `${baseUrl}/v1/documents`;

    const data = {
        file: http.file(pdfFile, 'Ask-Microsoft-transparency-FAQ.pdf', 'application/pdf'),
    };

    const params = {
        headers: {
            'Authorization': `Bearer ${token}`,
        },
        timeout: '30s',
    };

    const res = http.post(url, data, params);
    check(res, {
        'upload status is 200 or 202': (r) => r.status === 200 || r.status === 202,
    });

    sleep(3);
}