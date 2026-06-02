package sample.task;

import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/tasks")
public class TaskController {

    private final TaskService taskService;

    public TaskController(TaskService taskService) {
        this.taskService = taskService;
    }

    @GetMapping
    public List<Task> findTasks(
            @RequestParam Long ownerId,
            @RequestParam(defaultValue = "20") int limit
    ) {
        return taskService.findTasks(ownerId, limit);
    }

    @PatchMapping("/{taskId}/complete")
    public Task complete(
            @PathVariable Long taskId,
            @RequestParam Long ownerId
    ) {
        return taskService.complete(taskId, ownerId);
    }
}