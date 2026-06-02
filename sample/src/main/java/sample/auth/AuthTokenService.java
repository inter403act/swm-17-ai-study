package sample.auth;

import java.time.LocalDateTime;
import java.util.Objects;

public class AuthTokenService {

    public AuthToken verifyToken(AuthToken token) {
        if (token == null) {
            throw new IllegalArgumentException("Token is required");
        }

        LocalDateTime now = LocalDateTime.now();

        if (token.expiresAt().isBefore(now)) {
            throw new IllegalArgumentException("Token has expired");
        }

        return token;
    }

    public boolean isAdmin(AuthToken token) {
        AuthToken verifiedToken = verifyToken(token);
        return Objects.equals("ADMIN", verifiedToken.role());
    }
}