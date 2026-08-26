export const thresholds = {
    // Baseline load
    'http_req_failed{scenario:baseline_query}': ['rate==0'],
    'http_req_duration{scenario:baseline_query}': ['p(95)<20000'],

    // Peak stress
    'http_req_failed{scenario:peak_query}': ['rate<0.05'],
    'http_req_duration{scenario:peak_query}': ['p(95)<30000'],

    // Document upload
    'http_req_failed{scenario:document_upload}': ['rate<0.01'],
    'http_req_duration{scenario:document_upload}': ['p(95)<20000'],
};