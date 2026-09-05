package com.calderwood.meridian.security;

import internal.telemetry.Telemetry;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import java.util.Optional;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Turns the session token on a request into the signed-in member of staff.
 *
 * <p>The token arrives either in the {@code mrd_session} cookie, which is what the
 * console itself uses, or in an {@code Authorization: Bearer} header, which is what the
 * group's other back-office services use when they call this one on an operator's
 * behalf.
 */
@Component
public class SessionFilter extends OncePerRequestFilter {

    public static final String COOKIE = "mrd_session";

    private final TokenCodec tokens;
    private final ActorRepository actors;

    public SessionFilter(TokenCodec tokens, ActorRepository actors) {
        this.tokens = tokens;
        this.actors = actors;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        presented(request)
                .flatMap(tokens::verify)
                .flatMap(actors::byId)
                .ifPresent(actor -> {
                    UsernamePasswordAuthenticationToken authentication =
                            new UsernamePasswordAuthenticationToken(actor, null,
                                    List.of(new SimpleGrantedAuthority(actor.role().authority())));
                    SecurityContextHolder.getContext().setAuthentication(authentication);
                    request.setAttribute(CurrentActor.ATTRIBUTE, actor);
                    // So the request record says who was served, which is the first
                    // question asked of any of them.
                    Telemetry.authSubject(Long.toString(actor.id()));
                });
        try {
            chain.doFilter(request, response);
        } finally {
            SecurityContextHolder.clearContext();
        }
    }

    private static Optional<String> presented(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies != null) {
            for (Cookie cookie : cookies) {
                if (COOKIE.equals(cookie.getName()) && cookie.getValue() != null
                        && !cookie.getValue().isBlank()) {
                    return Optional.of(cookie.getValue());
                }
            }
        }
        String header = request.getHeader("Authorization");
        if (header != null && header.regionMatches(true, 0, "Bearer ", 0, 7)) {
            return Optional.of(header.substring(7).trim());
        }
        return Optional.empty();
    }
}
