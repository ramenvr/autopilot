// tools/net_smoke.py's test game: the player's controller. F attacks: the client asks its server, which hits the
// dummies in reach in front of the player (a client can't damage anything itself).

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"
#include "NetTestPlayerController.generated.h"

class UInputAction;
class UInputMappingContext;

UCLASS()
class ANetTestPlayerController : public APlayerController
{
	GENERATED_BODY()

public:
	/** The attacks the server counted */
	UPROPERTY(Replicated, BlueprintReadOnly, Category = "Attack")
	int32 Hits = 0;

	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

protected:
	virtual void SetupInputComponent() override;

	void Attack();

	UFUNCTION(Server, Reliable)
	void ServerAttack();

private:
	UPROPERTY()
	TObjectPtr<UInputAction> AttackAction;

	UPROPERTY()
	TObjectPtr<UInputMappingContext> AttackContext;
};
