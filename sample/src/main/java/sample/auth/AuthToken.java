package sample.auth;

import java.time.LocalDateTime;

public record AuthToken(
        Long userId,
        String role,
        LocalDateTime expiresAt
) {
}