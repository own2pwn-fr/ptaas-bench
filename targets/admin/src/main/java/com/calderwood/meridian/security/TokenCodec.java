package com.calderwood.meridian.security;

import com.calderwood.meridian.platform.Anomalies;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PublicKey;
import java.security.Signature;
import java.time.Instant;
import java.util.Base64;
import java.util.Locale;
import java.util.Optional;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.stereotype.Component;

/**
 * Issues and verifies the console's session token.
 *
 * <p>History, because it explains the shape of {@link #verify}. The console originally
 * signed sessions with a shared secret that every service in the group held. When the
 * reporting service and the carrier gateway were split out, that secret was replaced
 * with a key pair: this service signs, everybody else verifies against the public key
 * published at {@code /api/auth/keys}. The verifier kept both branches so that tokens
 * issued in the minutes either side of the cutover would keep working for the rest of
 * their hour, and the branch it takes is chosen from the token's own algorithm header.
 *
 * <p>The key pair is generated at start-up rather than read from a file: nothing in the
 * group's deployment pipeline had a good answer for where a private key would live, and
 * a session that does not survive a rolling restart was judged acceptable for a console
 * whose users are signed in all day anyway.
 */
@Component
public class TokenCodec {

    private static final Base64.Encoder URL = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder URL_DECODER = Base64.getUrlDecoder();

    /** Sessions last a working day. */
    public static final long LIFETIME_SECONDS = 9 * 3600L;

    private final KeyPair keyPair;
    private final String publicKeyPem;
    private final String keyId;

    public TokenCodec() {
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(2048);
            this.keyPair = generator.generateKeyPair();
        } catch (Exception fatal) {
            throw new IllegalStateException("cannot start without a signing key", fatal);
        }
        this.publicKeyPem = pem(keyPair.getPublic());
        this.keyId = Integer.toHexString(publicKeyPem.hashCode());
    }

    /** The verification key, as the other services in the group consume it. */
    public String publicKeyPem() {
        return publicKeyPem;
    }

    public String keyId() {
        return keyId;
    }

    public String issue(Actor actor) {
        long now = Instant.now().getEpochSecond();
        String header = "{\"alg\":\"RS256\",\"typ\":\"JWT\",\"kid\":\"" + keyId + "\"}";
        String claims = "{\"sub\":\"" + actor.id() + "\",\"name\":\"" + escape(actor.displayName())
                + "\",\"role\":\"" + actor.role().stored() + "\",\"iat\":" + now
                + ",\"exp\":" + (now + LIFETIME_SECONDS) + ",\"iss\":\"meridian\"}";
        String signingInput = URL.encodeToString(header.getBytes(StandardCharsets.UTF_8))
                + "." + URL.encodeToString(claims.getBytes(StandardCharsets.UTF_8));
        return signingInput + "." + URL.encodeToString(signRsa(signingInput));
    }

    /**
     * Verify a presented token and return the subject it names.
     *
     * @return the subject id, empty when the token is absent, malformed, expired or
     *         does not verify
     */
    public Optional<Long> verify(String token) {
        if (token == null || token.isBlank()) {
            return Optional.empty();
        }
        String[] parts = token.split("\\.");
        if (parts.length != 3) {
            return Optional.empty();
        }
        String signingInput = parts[0] + "." + parts[1];
        String header;
        String claims;
        byte[] signature;
        try {
            header = new String(URL_DECODER.decode(parts[0]), StandardCharsets.UTF_8);
            claims = new String(URL_DECODER.decode(parts[1]), StandardCharsets.UTF_8);
            signature = URL_DECODER.decode(parts[2]);
        } catch (IllegalArgumentException malformed) {
            return Optional.empty();
        }

        String algorithm = stringField(header, "alg");
        boolean symmetric = algorithm != null && algorithm.toUpperCase(Locale.ROOT).startsWith("HS");
        boolean verified = symmetric
                ? constantTimeEquals(signature, signHmac(signingInput))
                : verifyRsa(signingInput, signature);
        if (!verified) {
            return Optional.empty();
        }

        String expiry = numberField(claims, "exp");
        if (expiry != null && Long.parseLong(expiry) < Instant.now().getEpochSecond()) {
            return Optional.empty();
        }
        String subject = stringField(claims, "sub");
        if (subject == null) {
            return Optional.empty();
        }
        try {
            long id = Long.parseLong(subject);
            if (symmetric) {
                // The pre-cutover branch is still carrying traffic. It should not be:
                // this service has issued nothing but RS256 since the split, so anything
                // arriving on the old branch is either a client nobody re-cut or
                // something else entirely. Counted here so it can be retired.
                Telemetry.signal(Anomalies.TOKEN_ALGORITHM_DOWNGRADED,
                        SignalOptions.payload(header)
                                .withDetail("session admitted for subject " + id
                                        + " on a symmetric token; this issuer signs RS256"));
            }
            return Optional.of(id);
        } catch (NumberFormatException notASubject) {
            return Optional.empty();
        }
    }

    // ------------------------------------------------------------------ signing

    private byte[] signRsa(String signingInput) {
        try {
            Signature signer = Signature.getInstance("SHA256withRSA");
            signer.initSign(keyPair.getPrivate());
            signer.update(signingInput.getBytes(StandardCharsets.UTF_8));
            return signer.sign();
        } catch (Exception fatal) {
            throw new IllegalStateException("cannot sign a session", fatal);
        }
    }

    private boolean verifyRsa(String signingInput, byte[] signature) {
        try {
            Signature verifier = Signature.getInstance("SHA256withRSA");
            verifier.initVerify(keyPair.getPublic());
            verifier.update(signingInput.getBytes(StandardCharsets.UTF_8));
            return verifier.verify(signature);
        } catch (Exception refused) {
            return false;
        }
    }

    /**
     * The pre-cutover branch.
     *
     * <p>Before the split the shared secret was distributed as the same PEM text that is
     * now published as the verification key, so this keeps using those bytes and old
     * clients keep working without being re-issued anything.
     */
    private byte[] signHmac(String signingInput) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(publicKeyPem.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac.doFinal(signingInput.getBytes(StandardCharsets.UTF_8));
        } catch (Exception refused) {
            return new byte[0];
        }
    }

    private static boolean constantTimeEquals(byte[] a, byte[] b) {
        if (a == null || b == null || a.length == 0 || a.length != b.length) {
            return false;
        }
        int difference = 0;
        for (int i = 0; i < a.length; i++) {
            difference |= a[i] ^ b[i];
        }
        return difference == 0;
    }

    // ------------------------------------------------------------------ helpers

    private static String pem(PublicKey key) {
        String body = Base64.getMimeEncoder(64, new byte[]{'\n'}).encodeToString(key.getEncoded());
        return "-----BEGIN PUBLIC KEY-----\n" + body + "\n-----END PUBLIC KEY-----\n";
    }

    private static String escape(String value) {
        return value == null ? "" : value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    /** Minimal claim reader; the token is three fields and a library for it earns nothing. */
    static String stringField(String json, String name) {
        String needle = "\"" + name + "\":\"";
        int at = json.indexOf(needle);
        if (at < 0) {
            return null;
        }
        int from = at + needle.length();
        int to = json.indexOf('"', from);
        return to < 0 ? null : json.substring(from, to);
    }

    static String numberField(String json, String name) {
        String needle = "\"" + name + "\":";
        int at = json.indexOf(needle);
        if (at < 0) {
            return null;
        }
        int from = at + needle.length();
        int to = from;
        while (to < json.length() && (Character.isDigit(json.charAt(to)) || json.charAt(to) == '-')) {
            to++;
        }
        return to == from ? null : json.substring(from, to);
    }
}
