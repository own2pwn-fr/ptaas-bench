package internal.telemetry.spring;

import internal.telemetry.Telemetry;
import internal.telemetry.TelemetryClient;
import internal.telemetry.servlet.TelemetryFilter;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.core.Ordered;
import org.springframework.core.task.SimpleAsyncTaskExecutor;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Wires the agent into a Spring MVC service with no code in the service itself.
 *
 * <p>Everything here is conditional on the service not having said otherwise, so a host
 * that wants to build the client itself — a different service name, a fixed endpoint, a
 * narrower body budget — declares its own {@link TelemetryClient} bean and this
 * configuration steps aside.
 *
 * <p>Three things are wired:
 *
 * <ol>
 *   <li>the request filter, at the very front of the chain so the peer address is read
 *       before anything can rewrite it;</li>
 *   <li>the route interceptor, so a template is in scope while the handler runs;</li>
 *   <li>a decorator on every managed executor, so asynchronous work keeps the facts of
 *       the request that asked for it.</li>
 * </ol>
 */
@AutoConfiguration
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
public class TelemetryConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public TelemetryClient telemetryClient() {
        return Telemetry.init();
    }

    /**
     * Registered at the highest precedence available.
     *
     * <p>Being first is what keeps the peer address the socket's rather than a header's
     * in the ordinary case. The address resolution does not depend on winning that race
     * — it unwraps to the container's own request either way — but there is no reason to
     * rely on the fallback when the ordering can simply be right.
     */
    @Bean
    @ConditionalOnMissingBean(name = "telemetryFilterRegistration")
    public FilterRegistrationBean<TelemetryFilter> telemetryFilterRegistration(TelemetryClient client) {
        FilterRegistrationBean<TelemetryFilter> registration =
                new FilterRegistrationBean<>(new TelemetryFilter(client));
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        registration.addUrlPatterns("/*");
        registration.setName("telemetryFilter");
        return registration;
    }

    @Bean
    public WebMvcConfigurer telemetryInterceptorConfigurer() {
        return new WebMvcConfigurer() {
            @Override
            public void addInterceptors(InterceptorRegistry registry) {
                registry.addInterceptor(new RouteTemplateInterceptor()).order(Ordered.HIGHEST_PRECEDENCE);
            }
        };
    }

    @Bean
    public TelemetryTaskDecorator telemetryTaskDecorator() {
        return new TelemetryTaskDecorator();
    }

    /**
     * Installs the decorator on the executors the framework manages.
     *
     * <p>Done as a post-processor rather than by asking services to configure their own
     * executors, because the executors that matter are usually the ones a service never
     * declared: the default asynchronous executor, the one behind scheduled work, the
     * one a starter created.
     *
     * <p><strong>Before</strong> initialisation, not after. A pooled executor reads its
     * decorator once, while it is building the pool, and a decorator handed to it
     * afterwards is stored and never consulted — the wiring looks right, the tests that
     * check the bean exists pass, and every asynchronous record still comes back with no
     * request attached to it.
     */
    @Bean
    public static BeanPostProcessor telemetryExecutorPostProcessor() {
        return new BeanPostProcessor() {
            @Override
            public Object postProcessBeforeInitialization(Object bean, String beanName)
                    throws BeansException {
                try {
                    if (bean instanceof ThreadPoolTaskExecutor executor) {
                        executor.setTaskDecorator(new TelemetryTaskDecorator());
                    } else if (bean instanceof SimpleAsyncTaskExecutor executor) {
                        executor.setTaskDecorator(new TelemetryTaskDecorator());
                    }
                } catch (RuntimeException alreadyStarted) {
                    // An executor that refuses reconfiguration after start-up keeps its
                    // own behaviour; explicit wrapping still works at the call site.
                }
                return bean;
            }
        };
    }
}
