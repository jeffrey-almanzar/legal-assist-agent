import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  thresholds: {
    http_req_duration: ['p(95)<100', 'p(99)<200'],
    http_req_failed: ['rate<0.01'],
  },
  vus: 100,
  duration: '5m',
};

export default function () {
  let res = http.get('http://localhost:8000/');
  check(res, {
    'status was 200': (r) => r.status === 200,
  });
  sleep(1);
}