import { withQuery } from '$lib/query';

export interface loginRequest {
    pwd: string,
}

export interface jwtError {
    // "failure" or "banned"
    message: string,
    // Remaining trials
    remain: number,
    // IP unbanned after X seconds
    after: number,
}

interface authResponseMap {
    200: null,
    204: null,
    401: jwtError,
    403: jwtError,
    429: jwtError,
}

export const authApi = {
    login: withQuery
        .post(`/auth/login`)
        .request<loginRequest>()
        .response<authResponseMap>()
        .caller((
            pwd: string
        ) => ({
            body: {pwd: pwd},
        }))
        .build(),

    renew: withQuery
        .get("/auth/renew")
        .withOptions({credentials: "same-origin"})
        .response<authResponseMap>()
        .build(),

}
