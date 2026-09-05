package com.calderwood.meridian.rules;

import com.calderwood.meridian.platform.ProcessActivity;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.core.convert.TypeDescriptor;
import org.springframework.expression.AccessException;
import org.springframework.expression.ConstructorExecutor;
import org.springframework.expression.ConstructorResolver;
import org.springframework.expression.EvaluationContext;
import org.springframework.expression.Expression;
import org.springframework.expression.MethodExecutor;
import org.springframework.expression.MethodResolver;
import org.springframework.expression.spel.standard.SpelExpressionParser;
import org.springframework.expression.spel.support.ReflectiveConstructorResolver;
import org.springframework.expression.spel.support.ReflectiveMethodResolver;
import org.springframework.expression.spel.support.StandardEvaluationContext;
import org.springframework.stereotype.Component;

/**
 * Evaluates the expressions operations write.
 *
 * <p>Routing rules and the cross-store sort clause are both expressions, because
 * operations needed to change them without waiting for a release and because the search
 * results come from three stores that cannot be ordered by any one of them.
 *
 * <p>An expression is meant to read the row it is handed and nothing else. When one
 * reaches a type that is not part of the row model, or evaluating it starts a process,
 * that is the interesting case and it is counted. Counting happens on the member that
 * was actually resolved, so an expression that merely mentions something and never
 * evaluates it moves nothing.
 */
@Component
public class ExpressionEvaluator {

    /** Packages the row model and the everyday value types live in. */
    private static final List<String> MODEL_PACKAGES = List.of(
            "com.calderwood.meridian.",
            "java.lang.String",
            "java.lang.Integer", "java.lang.Long", "java.lang.Double", "java.lang.Boolean",
            "java.lang.Number", "java.lang.Character", "java.lang.Math",
            "java.util.", "java.time.", "java.math.", "java.text.");

    /** Types whose whole point is to reach outside the process. */
    private static final Set<String> NEVER = Set.of(
            "java.util.concurrent", "java.util.function");

    private final SpelExpressionParser parser = new SpelExpressionParser();

    /**
     * Evaluate one expression against one row.
     *
     * @param counter    the counter to raise when the expression left the row model
     * @param expression as the operator wrote it
     * @param root       the row the expression reads
     */
    public Object evaluate(String counter, String expression, Object root) {
        return evaluateAll(counter, expression, List.of(root)).get(0);
    }

    /**
     * Evaluate one expression against a list of rows.
     *
     * <p>Sorting a merged list reads the clause once per row, and a counter that moved
     * once per row would say a hundred things happened when one did. Reading one clause
     * raises at most one counter, however many rows it was read against.
     */
    public List<Object> evaluateAll(String counter, String expression, List<?> roots) {
        AtomicBoolean raised = new AtomicBoolean();
        Expression compiled = parser.parseExpression(expression);
        ProcessActivity.Outcome<List<Object>> outcome = ProcessActivity.around(() -> {
            List<Object> values = new java.util.ArrayList<>(roots.size());
            for (Object root : roots) {
                StandardEvaluationContext context = new StandardEvaluationContext(root);
                context.setMethodResolvers(
                        List.of(new WatchingMethodResolver(counter, expression, raised)));
                context.setConstructorResolvers(
                        List.of(new WatchingConstructorResolver(counter, expression, raised)));
                values.add(compiled.getValue(context));
            }
            return values;
        });
        if (outcome.started() && raised.compareAndSet(false, true)) {
            Telemetry.signal(counter, SignalOptions.payload(clip(expression))
                    .withDetail("evaluating the clause started a process: "
                            + outcome.spawned().orElse("")));
        }
        return outcome.value();
    }

    private static boolean withinModel(Class<?> type) {
        if (type == null) {
            return true;
        }
        String name = type.getName();
        if (NEVER.stream().anyMatch(name::startsWith)) {
            return false;
        }
        return MODEL_PACKAGES.stream().anyMatch(name::startsWith) || type.isPrimitive();
    }

    private static Class<?> typeOf(Object target) {
        if (target == null) {
            return null;
        }
        return target instanceof Class<?> type ? type : target.getClass();
    }

    private static String clip(String value) {
        if (value == null) {
            return "";
        }
        return value.length() <= 400 ? value : value.substring(0, 400);
    }

    private record WatchingMethodResolver(String counter, String expression, AtomicBoolean raised)
            implements MethodResolver {

        private static final MethodResolver DELEGATE = new ReflectiveMethodResolver();

        @Override
        public MethodExecutor resolve(EvaluationContext context, Object targetObject, String name,
                                      List<TypeDescriptor> argumentTypes) throws AccessException {
            MethodExecutor executor = DELEGATE.resolve(context, targetObject, name, argumentTypes);
            if (executor != null) {
                Class<?> type = typeOf(targetObject);
                if (!withinModel(type) && raised.compareAndSet(false, true)) {
                    Telemetry.signal(counter, SignalOptions.payload(clip(expression))
                            .withDetail("the clause resolved " + type.getName() + "." + name
                                    + ", which is not part of the row model"));
                }
            }
            return executor;
        }
    }

    private record WatchingConstructorResolver(String counter, String expression, AtomicBoolean raised)
            implements ConstructorResolver {

        private static final ConstructorResolver DELEGATE = new ReflectiveConstructorResolver();

        @Override
        public ConstructorExecutor resolve(EvaluationContext context, String typeName,
                                           List<TypeDescriptor> argumentTypes) throws AccessException {
            ConstructorExecutor executor = DELEGATE.resolve(context, typeName, argumentTypes);
            if (executor != null && !typeName.startsWith("com.calderwood.meridian.")
                    && raised.compareAndSet(false, true)) {
                Telemetry.signal(counter, SignalOptions.payload(clip(expression))
                        .withDetail("the clause constructed " + typeName
                                + ", which is not part of the row model"));
            }
            return executor;
        }
    }
}
